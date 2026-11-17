/**
 * Pi Agent Extension - Browser Bridge Model Provider
 *
 * Plugs into Pi Agent (@earendil-works/pi-coding-agent or @mariozechner/pi-coding-agent).
 *
 * UX:
 *  Registers Browser AI as a selectable model/provider in Pi Agent (`browser/browser-chat`).
 *  Normal prompts in a Pi session route directly to the active web chat tab (ChatGPT,
 *  Claude, Gemini, etc.) through the local Pi Bridge. No custom `/browser` prefix required.
 *
 * Install:
 *   mkdir -p ~/.pi/agent/extensions
 *   cp pi-extension.ts ~/.pi/agent/extensions/browser-bridge.ts
 *
 * Usage:
 *   pi --model browser/browser-chat
 *   or switch model inside an active session via `/model browser/browser-chat`.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

interface ModelInfo {
  id: string;
  name?: string;
  context_window?: number;
  max_tokens?: number;
}

interface ModelsResponse {
  data?: ModelInfo[];
}

export default async function (pi: ExtensionAPI) {
  const host = process.env.PI_BRIDGE_HOST || "127.0.0.1";
  const port = process.env.PI_HTTP_PORT || "17614";
  const baseUrl = process.env.PI_BRIDGE_BASE_URL || `http://${host}:${port}/v1`;

  const fallbackModels = [
    {
      id: "browser-chat",
      name: "Browser Chat (Active Tab)",
      reasoning: false,
      input: ["text"] as ("text" | "image")[],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 128000,
      maxTokens: 4096,
    },
  ];

  let models = fallbackModels;

  // Attempt dynamic discovery from bridge if available at startup
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1500);
    try {
      const res = await fetch(`${baseUrl}/models`, { signal: controller.signal });
      if (res.ok) {
        const payload = (await res.json()) as ModelsResponse;
        if (Array.isArray(payload?.data) && payload.data.length > 0) {
          models = payload.data.map((m) => ({
            id: m.id,
            name: m.name ?? (m.id === "browser-chat" ? "Browser Chat (Active Tab)" : m.id),
            reasoning: false,
            input: ["text"] as ("text" | "image")[],
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: m.context_window ?? 128000,
            maxTokens: m.max_tokens ?? 4096,
          }));
        }
      }
    } finally {
      clearTimeout(timer);
    }
  } catch {
    // If bridge is not running yet during startup, fallback to default model
    models = fallbackModels;
  }

  // Register browser AI provider
  pi.registerProvider("browser", {
    name: "Browser AI",
    baseUrl,
    apiKey: "browser-ai",
    api: "openai-completions",
    models,
  });

  // Notify user when browser model is selected
  pi.on("model_select", async (event, ctx) => {
    if (event.model.provider === "browser") {
      ctx.ui.notify(
        `Selected Browser AI model (${event.model.id}). Normal prompts route to active browser chat tab.`,
        "info"
      );
    }
  });

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.model?.provider === "browser") {
      ctx.ui.notify(
        `Browser AI model active (${ctx.model.id}). Normal prompts route to active browser chat tab.`,
        "info"
      );
    }
  });
}
