/**
 * Pi Agent Extension - Browser Bridge Integration
 *
 * Plugs into Pi Agent (@mariozechner/pi-coding-agent or @earendil-works/pi-coding-agent).
 *
 * Capabilities:
 *  1. Custom command: `/browser <prompt>`
 *     Sends a prompt from Pi terminal to the active AI chat in the browser,
 *     and prints the result back in Pi's terminal.
 *  2. Custom tool: `ask_browser`
 *     Allows Pi Agent to query the browser-based AI model during its own task.
 *
 * Install:
 *   Copy to ~/.pi/agent/extensions/browser-bridge.ts or .pi/extensions/browser-bridge.ts
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";

const BRIDGE_HTTP_URL = process.env.PI_HTTP_URL || "http://127.0.0.1:17614/prompt";

async function queryBrowserAI(prompt: string, timeoutMs: number = 300000): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(BRIDGE_HTTP_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, timeout: Math.floor(timeoutMs / 1000) }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error(`Bridge returned HTTP ${res.status}: ${err}`);
    }
    const data = await res.json();
    if (!data.ok) {
      throw new Error(data.error || "Unknown error from bridge");
    }
    return data.reply || "(no response)";
  } finally {
    clearTimeout(timer);
  }
}

export default function (pi: ExtensionAPI) {
  // Register /browser command for the user in Pi's terminal
  pi.registerCommand("browser", {
    description: "Send a prompt to the active Browser AI chat (ChatGPT, Claude, etc.)",
    handler: async (args, ctx) => {
      const prompt = (args || "").trim();
      if (!prompt) {
        ctx.ui.notify("Usage: /browser <prompt text>", "warning");
        return;
      }
      ctx.ui.notify("Sending prompt to browser AI...", "info");
      try {
        const reply = await queryBrowserAI(prompt);
        ctx.ui.notify("Received reply from browser AI", "success");
        // Print reply in terminal or send into session
        pi.sendUserMessage(`[Browser AI Reply]:\n${reply}`, { deliverAs: "followUp" });
      } catch (err: any) {
        ctx.ui.notify(`Failed: ${err.message}`, "error");
      }
    },
  });

  // Register custom tool for Pi Agent LLM to use
  pi.registerTool({
    name: "ask_browser",
    label: "Ask Browser AI",
    description: "Query the active Browser AI chat (Claude, ChatGPT, Gemini) for assistance or analysis.",
    promptSnippet: "Ask the browser-connected AI chatbot for assistance or code review",
    promptGuidelines: [
      "Use ask_browser when you want to consult the browser-based AI model (e.g. Claude or ChatGPT) for second opinions or complex explanations."
    ],
    parameters: Type.Object({
      prompt: Type.String({ description: "Question or instruction for the browser AI" }),
    }),
    async execute(toolCallId, params, signal, onUpdate, ctx) {
      onUpdate?.({
        content: [{ type: "text", text: "Waiting for browser AI response..." }],
      });
      try {
        const reply = await queryBrowserAI(params.prompt);
        return {
          content: [{ type: "text", text: reply }],
          details: { reply },
        };
      } catch (err: any) {
        return {
          content: [{ type: "text", text: `Error contacting browser AI: ${err.message}` }],
          isError: true,
        };
      }
    },
  });
}
