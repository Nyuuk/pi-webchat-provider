#!/usr/bin/env python3
"""
Pi Agent Bridge - WebSocket Bridge & Bidirectional Prompting Server.
Connects browser extensions (Claude, ChatGPT, Gemini, etc.) to the local
Pi Agent (harness) MCP server and provides a 2-way prompting channel.

Channels:
  1. Browser -> Pi:
     The browser AI issues commands -> Bridge routes them to Pi tools MCP server ->
     tool executes on host -> results returned to browser AI.
  2. Pi -> Browser:
     CLI / Pi Agent sends prompt to HTTP POST /prompt -> Bridge sends frame over WebSocket
     to extension -> extension types prompt into web chat & submits -> AI reply streamed/captured ->
     returned as HTTP response to Pi!
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import websockets
except ImportError:
    websockets = None

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "pi_bridge.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

WS_HOST = os.environ.get("PI_BRIDGE_HOST", "127.0.0.1")
WS_PORT = int(os.environ.get("PI_BRIDGE_PORT", "17613"))
HTTP_PORT = int(os.environ.get("PI_HTTP_PORT", "17614"))
BRIDGE_VERSION = "1.0.0"
MCP_PROTOCOL_VERSION = "2024-11-05"


def log(msg: str) -> None:
    logging.info(msg)
    print(f"[pi-bridge] {msg}", flush=True)


class MCPServerProcess:
    """Manages an MCP Server subprocess over stdio."""

    def __init__(self, server_id: str, command: str, args: List[str], cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None):
        self.server_id = server_id
        self.command = command
        self.args = args
        self.cwd = cwd or ROOT
        self.env = env or os.environ.copy()
        self.process: Optional[asyncio.subprocess.Process] = None
        self.tools: List[Dict[str, Any]] = []
        self._next_id = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self.ready = False

    @property
    def alive(self) -> bool:
        return bool(self.process and self.process.returncode is None and self.ready)

    async def start(self) -> bool:
        await self.stop()
        log(f"Launching MCP server '{self.server_id}': {self.command} {' '.join(self.args)}")
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                cwd=str(self.cwd),
                env=self.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=64 * 1024 * 1024,
            )
        except Exception as e:
            log(f"Failed to start {self.server_id}: {e}")
            return False

        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

        try:
            await self._request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pi-agent-bridge", "version": BRIDGE_VERSION},
            }, timeout=10)

            await self._notify("notifications/initialized")

            res = await self._request("tools/list", {}, timeout=10)
            raw_tools = res.get("tools", []) if isinstance(res, dict) else []
            self.tools = [t for t in raw_tools if isinstance(t, dict) and "name" in t]
            self.ready = True
            log(f"MCP server '{self.server_id}' ready ({len(self.tools)} tools loaded)")
            return True
        except Exception as e:
            log(f"Failed to initialize {self.server_id}: {e}")
            await self.stop()
            return False

    async def stop(self) -> None:
        self.ready = False
        tasks = [task for task in (self._reader_task, self._stderr_task) if task]
        self._reader_task = None
        self._stderr_task = None
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(f"Server {self.server_id} stopped"))
        self._pending.clear()

        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.process = None
        self.tools = []

    async def _send(self, payload: Dict[str, Any]) -> None:
        if not self.process or not self.process.stdin or self.process.returncode is not None:
            raise RuntimeError(f"Server {self.server_id} is not running")
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def _request(self, method: str, params: Dict[str, Any], timeout: float = 60.0) -> Any:
        req_id = self._next_id
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            res = await asyncio.wait_for(fut, timeout=timeout)
            if "error" in res:
                err = res["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise RuntimeError(msg)
            return res.get("result")
        finally:
            self._pending.pop(req_id, None)

    async def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def call_tool(self, name: str, arguments: Dict[str, Any], timeout: float = 120.0) -> Dict[str, Any]:
        return await self._request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)

    async def _read_stdout(self) -> None:
        if not self.process or not self.process.stdout:
            return
        while True:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    break
                msg = json.loads(line.decode("utf-8", errors="replace"))
                req_id = msg.get("id")
                if isinstance(req_id, int) and req_id in self._pending:
                    fut = self._pending[req_id]
                    if not fut.done():
                        fut.set_result(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log(f"[{self.server_id}] stdout parse error: {e}")

    async def _read_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return
        while True:
            try:
                line = await self.process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logging.info(f"[{self.server_id} stderr] {text}")
            except Exception:
                break


class PiBridge:
    def __init__(self):
        self.mcp_servers: Dict[str, MCPServerProcess] = {}
        self.clients: Dict[Any, float] = {}  # websocket -> connected_at
        self.client_lock = asyncio.Lock()
        self.pending_remote_prompts: Dict[str, asyncio.Future] = {}
        self.remote_prompt_lock = asyncio.Lock()

    def get_catalog(self) -> List[Dict[str, Any]]:
        catalog = []
        for server in self.mcp_servers.values():
            if server.alive:
                for tool in server.tools:
                    catalog.append({
                        "name": tool["name"],
                        "server": server.server_id,
                        "description": tool.get("description", ""),
                        "inputSchema": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    })
        return catalog

    def connected_message(self) -> Dict[str, Any]:
        return {
            "type": "connected",
            "bridge_version": BRIDGE_VERSION,
            "mcp_alive": any(s.alive for s in self.mcp_servers.values()),
            "tools": self.get_catalog(),
            "servers": [
                {"id": s.server_id, "alive": s.alive, "tools": len(s.tools)}
                for s in self.mcp_servers.values()
            ],
        }

    async def broadcast(self, message: Dict[str, Any]) -> None:
        async with self.client_lock:
            clients = list(self.clients.keys())
        if not clients:
            return
        payload = json.dumps(message, ensure_ascii=False)
        for client in clients:
            try:
                await client.send(payload)
            except Exception:
                pass

    async def handle_ws_client(self, websocket: Any, path: Optional[str] = None) -> None:
        async with self.client_lock:
            self.clients[websocket] = time.time()
        log("Browser extension connected")
        try:
            await websocket.send(json.dumps(self.connected_message(), ensure_ascii=False))
            async for raw in websocket:
                try:
                    req = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(req, dict):
                    continue

                req_type = req.get("type")
                req_id = req.get("id")

                if req_type == "ping":
                    await websocket.send(json.dumps({"type": "pong", "id": req_id}))
                elif req_type == "studio_status":
                    # Handle gracefully for compatibility
                    await websocket.send(json.dumps({"type": "studio_status", "id": req_id, "ok": True, "studio": False}))
                elif req_type == "list_tools":
                    msg = self.connected_message()
                    msg["type"] = "tools"
                    msg["id"] = req_id
                    await websocket.send(json.dumps(msg, ensure_ascii=False))
                elif req_type == "call_tool":
                    await self._handle_call_tool(websocket, req)
                elif req_type == "remote_prompt_result":
                    # Correlation from browser extension completing a Pi-initiated prompt!
                    p_id = req.get("id")
                    reply = req.get("reply", "")
                    error = req.get("error")
                    if p_id and p_id in self.pending_remote_prompts:
                        fut = self.pending_remote_prompts[p_id]
                        if not fut.done():
                            if error:
                                fut.set_exception(RuntimeError(error))
                            else:
                                fut.set_result(reply)
                elif req_type == "restart_mcp":
                    await self.restart_servers()
                    await websocket.send(json.dumps({
                        "type": "mcp_status",
                        "id": req_id,
                        "ok": True,
                        "alive": any(s.alive for s in self.mcp_servers.values()),
                        "tools": self.get_catalog(),
                    }, ensure_ascii=False))
        finally:
            async with self.client_lock:
                self.clients.pop(websocket, None)
            log("Browser extension disconnected")

    async def _handle_call_tool(self, websocket: Any, req: Dict[str, Any]) -> None:
        req_id = req.get("id")
        name = req.get("name")
        arguments = req.get("arguments") or {}
        timeout_ms = req.get("timeout", 120000)
        timeout = max(1.0, min(float(timeout_ms) / 1000.0, 600.0))

        server_id = req.get("server")
        candidates = [
            server for server in self.mcp_servers.values()
            if server.alive and any(tool["name"] == name for tool in server.tools)
        ]
        target_server = None
        if server_id:
            target_server = self.mcp_servers.get(server_id)
            if not target_server or not target_server.alive or not any(tool["name"] == name for tool in target_server.tools):
                target_server = None
        elif len(candidates) == 1:
            target_server = candidates[0]

        if not target_server:
            reason = "unknown_tool" if not candidates else "ambiguous_tool"
            message = (
                f"Tool '{name}' not found on any active MCP server."
                if not candidates else
                f"Tool '{name}' is available on multiple MCP servers; specify a server."
            )
            resp = {
                "type": "tool_result",
                "id": req_id,
                "ok": False,
                "kind": reason,
                "error": message,
            }
            await websocket.send(json.dumps(resp, ensure_ascii=False))
            return

        try:
            result = await target_server.call_tool(name, arguments, timeout=timeout)
            content = result.get("content", []) if isinstance(result, dict) else []
            text = "\n".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
            images = [
                item
                for item in content
                if isinstance(item, dict) and item.get("type") == "image"
            ]
            is_error = bool(result.get("isError")) if isinstance(result, dict) else False
            resp = {
                "type": "tool_result",
                "id": req_id,
                "ok": not is_error,
                "text": text,
                "images": images,
                "kind": "tool_error" if is_error else None,
                "error": text if is_error else None,
            }
        except Exception as e:
            resp = {
                "type": "tool_result",
                "id": req_id,
                "ok": False,
                "kind": "tool_error",
                "error": f"Execution error: {e}",
            }
        await websocket.send(json.dumps(resp, ensure_ascii=False))

    async def send_remote_prompt(self, prompt: str, timeout: float = 300.0) -> str:
        """Sends a prompt from Pi host into the browser AI chat and waits for final reply."""
        async with self.remote_prompt_lock:
            async with self.client_lock:
                clients = list(self.clients.keys())
            if not clients:
                raise RuntimeError("No browser extension is currently connected. Open a supported AI chat tab.")

            # Deterministic single-client routing: route to the most recently connected client
            if len(clients) > 1:
                log(f"[remote_prompt] Warning: Ambiguity detected with {len(clients)} connected extension clients. Routing prompt to the most recently connected client to avoid double-submit.")

            target_client = clients[-1]
            req_id = f"prompt_{int(asyncio.get_running_loop().time() * 1000)}_{uuid.uuid4().hex[:6]}"
            fut = asyncio.get_running_loop().create_future()
            self.pending_remote_prompts[req_id] = fut
            msg = {"type": "remote_prompt", "id": req_id, "text": prompt}
            payload = json.dumps(msg, ensure_ascii=False)
            try:
                await target_client.send(payload)
                try:
                    return await asyncio.wait_for(fut, timeout=timeout)
                except asyncio.TimeoutError as e:
                    raise RuntimeError("Timed out waiting for browser AI response") from e
            except Exception as e:
                if isinstance(e, RuntimeError) and str(e) == "Timed out waiting for browser AI response":
                    raise
                raise RuntimeError(f"Failed to send remote prompt to browser extension: {e}") from e
            finally:
                self.pending_remote_prompts.pop(req_id, None)

    async def start_servers(self) -> None:
        config = self._load_config()
        servers = config.get("mcpServers", {})
        if not servers:
            # Default to pi_tools_mcp.py
            servers = {
                "pi-agent": {
                    "command": sys.executable,
                    "args": [str(ROOT / "pi_tools_mcp.py")],
                }
            }

        for sid, scfg in servers.items():
            cmd = scfg.get("command")
            if not isinstance(cmd, str) or not cmd.strip():
                log(f"Skipping MCP server '{sid}': missing command")
                continue
            args = scfg.get("args", [])
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                log(f"Skipping MCP server '{sid}': args must be a list of strings")
                continue
            env = os.environ.copy()
            configured_env = scfg.get("env", {})
            if not isinstance(configured_env, dict):
                log(f"Skipping MCP server '{sid}': env must be an object")
                continue
            env.update({str(key): str(value) for key, value in configured_env.items()})
            # Keep the MCP process on the same interpreter environment as bridge.
            if cmd == "python3":
                cmd = sys.executable
            proc = MCPServerProcess(sid, cmd, args, cwd=ROOT, env=env)
            self.mcp_servers[sid] = proc
            await proc.start()

    async def restart_servers(self) -> None:
        for s in self.mcp_servers.values():
            await s.stop()
        self.mcp_servers.clear()
        await self.start_servers()
        await self.broadcast(self.connected_message())

    def _load_config(self) -> Dict[str, Any]:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                log(f"Warning: could not read config.json: {e}")
        return {}


# ── HTTP Server for Direction 2 (Pi -> Browser) ───────────────────────────
class SimpleHttpServer:
    def __init__(self, bridge: PiBridge, host: str, port: int):
        self.bridge = bridge
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
        log(f"HTTP REST API listening on http://{self.host}:{self.port}")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            header_blob = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            if len(header_blob) > 16384:
                self._write_json(writer, 400, {"error": "HTTP headers too large"})
                return
            header_lines = header_blob.decode("utf-8", errors="replace").split("\r\n")
            req_line = header_lines[0]
            parts = req_line.split()
            method = parts[0] if parts else "GET"
            path = parts[1] if len(parts) > 1 else "/"

            content_length = 0
            headers = {}
            for h in header_lines[1:]:
                if ":" in h:
                    key, value = h.split(":", 1)
                    key = key.strip().lower()
                    if key in headers and key in ("content-length", "transfer-encoding"):
                        self._write_json(writer, 400, {"error": f"Duplicate {key} header"})
                        return
                    headers[key] = value.strip()
            if "content-length" in headers:
                try:
                    content_length = int(headers["content-length"])
                except ValueError:
                    self._write_json(writer, 400, {"error": "Invalid Content-Length"})
                    return
            if content_length < 0 or content_length > 8 * 1024 * 1024:
                self._write_json(writer, 413, {"error": "Request body too large"})
                return
            if "transfer-encoding" in headers:
                self._write_json(writer, 400, {"error": "Transfer-Encoding request bodies are not supported"})
                return

            body = await asyncio.wait_for(reader.readexactly(content_length), timeout=15) if content_length else b""
            await self._route(method, path, body, writer)
        except asyncio.IncompleteReadError:
            self._write_json(writer, 400, {"error": "Incomplete HTTP request body"})
        except asyncio.LimitOverrunError:
            self._write_json(writer, 400, {"error": "HTTP headers too large"})
        except asyncio.TimeoutError:
            self._write_json(writer, 408, {"error": "HTTP request timed out"})
        except Exception as e:
            self._write_json(writer, 500, {"error": str(e)})
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _extract_prompt_text(self, messages: List[Dict[str, Any]]) -> str:
        """Keep only the newest user turn and subsequent assistant/tool context."""
        if not messages:
            return ""
        last_user_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break
        candidates = messages[last_user_idx:] if last_user_idx is not None else messages[-1:]
        parts = []
        for msg in candidates:
            role = msg.get("role")
            content = msg.get("content", "")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                sub = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            sub.append(item.get("text", ""))
                        elif item.get("type") == "image_url":
                            sub.append(f"[Image: {item.get('image_url', {}).get('url', '')}]")
                    elif isinstance(item, str):
                        sub.append(item)
                text = " ".join(sub)
            elif content is not None:
                text = str(content)

            text = text.strip()
            if role == "tool":
                tool_name = msg.get("name") or msg.get("tool_call_id") or "tool"
                if text:
                    parts.append(f"[Tool Output ({tool_name})]:\n{text}")
            elif role == "user":
                if text:
                    parts.append(f"[User Request]:\n{text}")
            elif role == "assistant":
                if text:
                    parts.append(f"[Assistant]:\n{text}")
                for call in msg.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    name = fn.get("name") or call.get("id") or "tool"
                    args = fn.get("arguments", "")
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    parts.append(f"[Assistant Tool Call ({name})]:\n{args}")

        return "\n\n".join(parts)

    def _write_openai_error(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        message: str,
        err_type: str = "invalid_request_error",
        code: Optional[str] = None,
    ) -> None:
        data = {
            "error": {
                "message": message,
                "type": err_type,
                "param": None,
                "code": code,
            }
        }
        self._write_json(writer, status, data)

    async def _route(self, method: str, path: str, body: bytes, writer: asyncio.StreamWriter) -> None:
        if method == "OPTIONS":
            self._write_json(writer, 200, {"ok": True})
            return

        if method == "GET" and path == "/health":
            self._write_json(writer, 200, {
                "ok": True,
                "version": BRIDGE_VERSION,
                "extension_connected": len(self.bridge.clients) > 0,
                "clients_count": len(self.bridge.clients),
                "tools_count": len(self.bridge.get_catalog()),
                "mcp_alive": any(s.alive for s in self.bridge.mcp_servers.values()),
            })
        elif method == "GET" and path == "/tools":
            self._write_json(writer, 200, {
                "tools": self.bridge.get_catalog()
            })
        elif method == "GET" and path in ("/v1/models", "/models"):
            self._write_json(writer, 200, {
                "object": "list",
                "data": [
                    {
                        "id": "browser-chat",
                        "object": "model",
                        "created": 1700000000,
                        "owned_by": "browser-ai",
                        "permission": [],
                        "root": "browser-chat",
                        "parent": None,
                    }
                ],
            })
        elif method == "POST" and path in ("/v1/chat/completions", "/chat/completions"):
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                self._write_openai_error(writer, 400, "Invalid JSON body", "invalid_request_error")
                return
            if not isinstance(data, dict):
                self._write_openai_error(writer, 400, "JSON body must be an object", "invalid_request_error")
                return

            messages = data.get("messages", [])
            if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
                self._write_openai_error(writer, 400, "messages must be an array of objects", "invalid_request_error")
                return
            stream = bool(data.get("stream", False))
            model = data.get("model", "browser-chat")

            # Extract current latest input context (user message or tool results)
            prompt_text = self._extract_prompt_text(messages)
            if not prompt_text:
                self._write_openai_error(writer, 400, "No prompt content found in messages", "invalid_request_error")
                return

            try:
                timeout = float(data.get("timeout", 300))
                if not 10 <= timeout <= 600:
                    raise ValueError("timeout must be between 10 and 600 seconds")
            except (TypeError, ValueError):
                self._write_openai_error(writer, 400, "timeout must be numeric and between 10 and 600 seconds")
                return
            try:
                reply = await self.bridge.send_remote_prompt(prompt_text, timeout=timeout)
                created_ts = int(time.time())
                cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

                if stream:
                    await self._write_sse_chunks(writer, cmpl_id, model, reply, created_ts)
                else:
                    self._write_json(writer, 200, {
                        "id": cmpl_id,
                        "object": "chat.completion",
                        "created": created_ts,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": reply,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": len(prompt_text.split()),
                            "completion_tokens": len(reply.split()),
                            "total_tokens": len(prompt_text.split()) + len(reply.split()),
                        },
                    })
            except Exception as e:
                self._write_openai_error(writer, 500, str(e), "server_error")
        elif method == "POST" and path == "/prompt":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                self._write_json(writer, 400, {"error": "Invalid JSON body"})
                return
            if not isinstance(data, dict):
                self._write_json(writer, 400, {"error": "JSON body must be an object"})
                return

            prompt = data.get("prompt", "")
            if not isinstance(prompt, str):
                self._write_json(writer, 400, {"error": "'prompt' field must be a string"})
                return
            prompt_text = prompt.strip()
            if not prompt_text:
                self._write_json(writer, 400, {"error": "'prompt' field is required"})
                return

            try:
                timeout = float(data.get("timeout", 300))
                if not 10 <= timeout <= 600:
                    raise ValueError("timeout must be between 10 and 600 seconds")
            except (TypeError, ValueError):
                self._write_json(writer, 400, {"error": "timeout must be numeric and between 10 and 600 seconds"})
                return
            try:
                reply = await self.bridge.send_remote_prompt(prompt_text, timeout=timeout)
                self._write_json(writer, 200, {"ok": True, "reply": reply})
            except Exception as e:
                self._write_json(writer, 500, {"ok": False, "error": str(e)})
        else:
            self._write_json(writer, 404, {"error": f"Not found: {method} {path}"})

    async def _write_sse_chunks(self, writer: asyncio.StreamWriter, cmpl_id: str, model: str, reply: str, created_ts: int) -> None:
        try:
            header = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/event-stream; charset=utf-8\r\n"
                "Cache-Control: no-cache\r\n"
                "Connection: close\r\n\r\n"
            ).encode("utf-8")
            writer.write(header)
            await writer.drain()

            # Chunk 1: Role
            c1 = {
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            writer.write(f"data: {json.dumps(c1, ensure_ascii=False)}\n\n".encode("utf-8"))
            await writer.drain()

            # Chunk 2: Content
            c2 = {
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": reply or ""}, "finish_reason": None}],
            }
            writer.write(f"data: {json.dumps(c2, ensure_ascii=False)}\n\n".encode("utf-8"))
            await writer.drain()

            # Chunk 3: Stop
            c3 = {
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            writer.write(f"data: {json.dumps(c3, ensure_ascii=False)}\n\n".encode("utf-8"))
            writer.write(b"data: [DONE]\n\n")
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass

    def _write_json(self, writer: asyncio.StreamWriter, status: int, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        status_text = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
        resp = (
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8") + payload
        try:
            writer.write(resp)
        except Exception:
            pass


async def main():
    if not websockets:
        log("ERROR: 'websockets' package is not installed. Install via pip: pip install websockets")
        sys.exit(1)

    bridge = PiBridge()
    await bridge.start_servers()

    # Start WebSocket server
    ws_server = await websockets.serve(
        bridge.handle_ws_client,
        WS_HOST,
        WS_PORT,
        max_size=128 * 1024 * 1024,
        ping_interval=30,
        ping_timeout=90,
    )
    log(f"WebSocket Bridge listening on ws://{WS_HOST}:{WS_PORT}")

    # Start HTTP REST server
    http_server = SimpleHttpServer(bridge, WS_HOST, HTTP_PORT)
    await http_server.start()

    log("Pi Agent Connector Bridge is ready and active.")
    try:
        await asyncio.Future()  # run forever
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        ws_server.close()
        await ws_server.wait_closed()
        for s in bridge.mcp_servers.values():
            await s.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Bridge shut down.")
