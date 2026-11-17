#!/usr/bin/env python3
"""
CLI tool to send a prompt from Pi terminal to the active Browser AI chat (Direction 2).
Talks to the Pi Bridge HTTP API on http://127.0.0.1:17614/prompt.

Usage:
  python3 send_prompt.py "Periksa file main.py dan jelaskan arsitekturnya"
  git diff | python3 send_prompt.py "Review diff ini"
"""

import json
import os
import sys
import urllib.error
import urllib.request

HTTP_HOST = os.environ.get("PI_BRIDGE_HOST", "127.0.0.1")
HTTP_PORT = int(os.environ.get("PI_HTTP_PORT", "17614"))
ENDPOINT = f"http://{HTTP_HOST}:{HTTP_PORT}/prompt"


def main():
    piped_input = ""
    if not sys.stdin.isatty():
        piped_input = sys.stdin.read().strip()

    args_prompt = " ".join(sys.argv[1:]).strip()

    if not args_prompt and not piped_input:
        print("Usage: python3 send_prompt.py \"<prompt text>\"", file=sys.stderr)
        print("   or: cat file.txt | python3 send_prompt.py \"<instruction>\"", file=sys.stderr)
        sys.exit(1)

    full_prompt = args_prompt
    if piped_input:
        if full_prompt:
            full_prompt += "\n\nContext:\n" + piped_input
        else:
            full_prompt = piped_input

    payload = json.dumps({"prompt": full_prompt, "timeout": 300}).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"[*] Sending prompt to browser AI chat via {ENDPOINT}...")
    try:
        with urllib.request.urlopen(req, timeout=360) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                print("\n[AI Response]:")
                print("=" * 60)
                print(data.get("reply", ""))
                print("=" * 60)
            else:
                print(f"[!] Error from bridge: {data.get('error')}", file=sys.stderr)
                sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[!] Connection failed: {e}. Is pi_bridge.py running?", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
