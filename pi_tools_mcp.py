#!/usr/bin/env python3
"""
Pi Agent Tools - Model Context Protocol (MCP) Server.
Implements the native Pi Agent (harness) toolset:
  - bash: execute shell commands
  - read: read file with line numbers, offset, and limit
  - write: write complete file contents
  - edit: exact find-and-replace edits
  - grep: regex content search across files
  - find: file search by glob pattern
  - ls: directory listing
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "pi-agent-tools"
SERVER_VERSION = "1.0.0"

IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".next", "dist", "build",
}

ROOT_DIR = Path(os.environ.get("PI_WORKSPACE_ROOT", os.getcwd())).resolve()


def _resolve_path(rel_or_abs: str) -> Path:
    """Resolve a path and refuse to escape the configured workspace."""
    p = Path(rel_or_abs.strip() or ".")
    if not p.is_absolute():
        p = ROOT_DIR / p
    target = p.resolve()
    try:
        target.relative_to(ROOT_DIR)
    except ValueError as error:
        raise ValueError(f"Path escapes workspace root: {rel_or_abs}") from error
    return target


def _relative(p: Path) -> str:
    try:
        return p.relative_to(ROOT_DIR).as_posix() or "."
    except ValueError:
        return str(p)


def tool_bash(command: str, timeout: int = 60) -> str:
    if not command:
        raise ValueError("command parameter is required")
    timeout = max(1, min(int(timeout), 600))
    proc = subprocess.run(
        ["/usr/bin/env", "bash", "-c", command],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if proc.returncode != 0:
        parts.append(f"[exit status: {proc.returncode}]")
    return "\n\n".join(parts) if parts else "(no output)"


def tool_read(path: str, offset: int = 1, limit: int = 2000) -> str:
    target = _resolve_path(path)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"Target is a directory, not a file: {path}")

    offset = max(1, int(offset))
    limit = max(1, min(int(limit), 5000))

    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        raise RuntimeError(f"Could not read {path}: {e}")

    total_lines = len(lines)
    start_idx = offset - 1
    if start_idx >= total_lines:
        return f"File has {total_lines} lines; offset {offset} is past the end."
    end_idx = min(start_idx + limit, total_lines)

    selected = lines[start_idx:end_idx]
    formatted = [f"{start_idx + i + 1:4d} | {line}" for i, line in enumerate(selected)]

    header = f"--- {target.name} ({total_lines} lines total, showing {start_idx + 1}-{end_idx}) ---"
    return header + "\n" + "\n".join(formatted)


def tool_write(path: str, content: str) -> str:
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    lines_count = len(content.splitlines())
    bytes_count = len(content.encode("utf-8"))
    return f"Successfully wrote {bytes_count} bytes ({lines_count} lines) to {_relative(target)}"


def tool_edit(path: str, edits: List[Dict[str, str]]) -> str:
    target = _resolve_path(path)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"File not found for edit: {path}")

    content = target.read_text(encoding="utf-8", errors="replace")
    original = content
    applied = 0

    for i, edit in enumerate(edits):
        old_text = edit.get("oldText", "")
        new_text = edit.get("newText", "")
        if not old_text:
            raise ValueError(f"Edit #{i+1} missing oldText")

        count = content.count(old_text)
        if count == 0:
            raise ValueError(f"Edit #{i+1}: oldText not found in {path}")
        if count > 1:
            raise ValueError(f"Edit #{i+1}: oldText is ambiguous ({count} occurrences) in {path}")

        content = content.replace(old_text, new_text, 1)
        applied += 1

    if content != original:
        target.write_text(content, encoding="utf-8")
        return f"Successfully applied {applied} edit(s) to {_relative(target)}"
    return f"No changes made to {_relative(target)}"


def tool_grep(pattern: str, path: str = ".", glob: Optional[str] = None) -> str:
    target = _resolve_path(path)
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    try:
        regex = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern '{pattern}': {e}")

    results = []
    max_results = 200

    def check_file(fpath: Path):
        nonlocal results
        if len(results) >= max_results:
            return
        if glob and not fnmatch.fnmatch(fpath.name, glob):
            return
        try:
            for lnum, line in enumerate(fpath.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if regex.search(line):
                    results.append(f"{_relative(fpath)}:{lnum}: {line.strip()[:200]}")
                    if len(results) >= max_results:
                        return
        except Exception:
            pass

    if target.is_file():
        check_file(target)
    else:
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for f in sorted(files):
                check_file(Path(root) / f)
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

    if not results:
        return f"No matches found for pattern '{pattern}'"
    out = "\n".join(results)
    if len(results) >= max_results:
        out += f"\n... (truncated at {max_results} results)"
    return out


def tool_find(pattern: str, path: str = ".") -> str:
    target = _resolve_path(path)
    if not target.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {path}")

    results = []
    max_results = 300
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for name in sorted(dirs + files):
            if fnmatch.fnmatch(name, pattern):
                p = Path(root) / name
                kind = "[dir]" if p.is_dir() else "[file]"
                results.append(f"{kind} {_relative(p)}")
                if len(results) >= max_results:
                    break
        if len(results) >= max_results:
            break

    if not results:
        return f"No files matching '{pattern}' in {path}"
    out = "\n".join(results)
    if len(results) >= max_results:
        out += f"\n... (truncated at {max_results} items)"
    return out


def tool_ls(path: str = ".") -> str:
    target = _resolve_path(path)
    if not target.exists():
        raise FileNotFoundError(f"Directory not found: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    items = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name in IGNORED_DIRS:
            continue
        kind = "dir " if entry.is_dir() else "file"
        size = ""
        if entry.is_file():
            try:
                s = entry.stat().st_size
                size = f" ({s} B)"
            except OSError:
                pass
        items.append(f"[{kind}] {entry.name}{size}")

    if not items:
        return f"Directory is empty: {_relative(target)}"
    return f"Contents of {_relative(target)}:\n" + "\n".join(items)


TOOLS_DEFINITIONS = [
    {
        "name": "bash",
        "description": "Execute a shell command inside the workspace using bash. Returns stdout, stderr, and exit code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60, max 600)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read",
        "description": "Read contents of a file with line numbers, offset, and limit.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute path to the file"},
                "offset": {"type": "integer", "description": "1-based line number to start reading from (default 1)"},
                "limit": {"type": "integer", "description": "Maximum number of lines to return (default 2000)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write",
        "description": "Create or overwrite a file with given text content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute path to the file"},
                "content": {"type": "string", "description": "Full text content to write into the file"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit",
        "description": "Apply targeted exact find-and-replace edits to a file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit"},
                "edits": {
                    "type": "array",
                    "description": "Ordered list of {oldText, newText} exact replacements",
                    "items": {
                        "type": "object",
                        "properties": {
                            "oldText": {"type": "string", "description": "Exact text to find and replace"},
                            "newText": {"type": "string", "description": "Replacement text"},
                        },
                        "required": ["oldText", "newText"],
                    },
                },
            },
            "required": ["path", "edits"],
        },
    },
    {
        "name": "grep",
        "description": "Search regex pattern across files in the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression pattern to search for"},
                "path": {"type": "string", "description": "Path to directory or file (default '.')"},
                "glob": {"type": "string", "description": "Optional file glob filter, e.g. '*.py' or '*.ts'"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "find",
        "description": "Search for files and directories matching a glob pattern.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.ts', '*config*')"},
                "path": {"type": "string", "description": "Directory to search in (default '.')"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "ls",
        "description": "List directory contents with file types and sizes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list (default '.')"},
            },
        },
    },
]


def execute_tool(name: str, arguments: Dict[str, Any]) -> str:
    if name == "bash":
        return tool_bash(
            command=arguments.get("command", ""),
            timeout=arguments.get("timeout", 60),
        )
    elif name == "read":
        return tool_read(
            path=arguments.get("path", ""),
            offset=arguments.get("offset", 1),
            limit=arguments.get("limit", 2000),
        )
    elif name == "write":
        return tool_write(
            path=arguments.get("path", ""),
            content=arguments.get("content", ""),
        )
    elif name == "edit":
        return tool_edit(
            path=arguments.get("path", ""),
            edits=arguments.get("edits", []),
        )
    elif name == "grep":
        return tool_grep(
            pattern=arguments.get("pattern", ""),
            path=arguments.get("path", "."),
            glob=arguments.get("glob"),
        )
    elif name == "find":
        return tool_find(
            pattern=arguments.get("pattern", "*"),
            path=arguments.get("path", "."),
        )
    elif name == "ls":
        return tool_ls(
            path=arguments.get("path", "."),
        )
    else:
        raise ValueError(f"Unknown Pi Agent tool: {name}")


def _send_response(response: Dict[str, Any]) -> None:
    line = json.dumps(response, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


def main():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

        if not isinstance(msg, dict):
            continue

        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}

        if method == "initialize":
            _send_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            })
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            _send_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS_DEFINITIONS},
            })
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            try:
                output = execute_tool(tool_name, tool_args)
                _send_response({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": output}],
                        "isError": False,
                    },
                })
            except Exception as e:
                _send_response({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                })
        elif method == "ping":
            _send_response({"jsonrpc": "2.0", "id": req_id, "result": {}})
        else:
            if req_id is not None:
                _send_response({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })


if __name__ == "__main__":
    main()
