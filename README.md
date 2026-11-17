# Pi Webchat Provider

A Pi Agent package that exposes a browser-hosted AI chat as a selectable model provider. It also connects supported browser chats to local tools through a localhost bridge.

## Install

Clone or download this repository, then install the local Pi package:

```bash
git clone https://github.com/Nyuuk/pi-webchat-provider.git
cd pi-webchat-provider
pi install .
```

`pi install .` registers the package for your personal Pi configuration. The package manifest points Pi to `pi-extension.ts`; it adds no npm dependencies because the provider uses Pi's built-in extension API.

To try it for one Pi invocation without adding it to settings:

```bash
pi -e .
```

To remove the personal package later:

```bash
pi remove /absolute/path/to/pi-webchat-provider
```

## Start the local bridge

The bridge needs Python 3 and the `websockets` package. The launcher does not install into system Python automatically. On Arch Linux:

```bash
sudo pacman -S python-websockets
```

On other systems, install `websockets` in the Python environment that will run the bridge. Then start it from the cloned directory:

```bash
./start.sh
```

Keep the bridge terminal open. It listens only on loopback:

- WebSocket: `127.0.0.1:17613` — browser extension connection.
- HTTP: `127.0.0.1:17614` — Pi model-provider requests.

## Load the browser extension

1. Open `chrome://extensions` (Chrome) or `edge://extensions` (Edge).
2. Enable **Developer mode** and choose **Load unpacked**.
3. Select the `extension/` directory inside this clone.
4. Open a supported AI chat tab, sign in, and click **Start** in the Pi Agent Connector bar.

The browser session must be initialized with **Start** before Pi sends a prompt to it. Requests are rejected if the chat is uninitialized or currently busy.

## Use the Browser AI model in Pi

After `pi install .`, restart Pi or reload extensions and select:

```text
/model browser/browser-chat
```

Or launch Pi with that model selected:

```bash
pi --model browser/browser-chat
```

Then type normal prompts in Pi. They are sent to the active supported browser chat; no `/browser` prefix is needed.

## Browser-to-host tools

With the browser session initialized, its AI can use the configured MCP tools (`bash`, `read`, `write`, `edit`, `grep`, `find`, `ls`). File path tools are constrained to `PI_WORKSPACE_ROOT` (defaults to this package's directory). **`bash` runs with the host account's permissions and is not sandboxed to the workspace; commands are not individually confirmed.** Use only trusted AI accounts and review commands/results. Do not expose bridge ports to LAN or the internet.

## Stop / remove

- Stop the bridge with `Ctrl+C` in its terminal.
- To uninstall the package from Pi: `pi remove /absolute/path/to/pi-webchat-provider`.
- Disable or remove the unpacked extension from the browser's extension settings when no longer needed.

Runtime/browser integration should be tested by the user after setup; the package has not been runtime-tested in this environment.
