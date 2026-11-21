<p align="center">
  <img src="assets/tzara_banner.png" alt="Tzara banner" width="100%">
</p>

# Tzara: Your Cute Personal AI Agent

Tzara is an open-source, **local-first personal AI agent** built on **LangChain's DeepAgents framework** and designed for developers who want to learn and experiment with AI agents in Python.

It's fully local, supports multiple models, comes with a calculator tool, and is extensible with custom tools.

**No cloud. Complete data privacy.**


## Features

- [x] **Lightweight & low footprint** — Small Python core with fast startup and low memory usage, runs comfortably on a personal laptop
- [x] **Multi-Model Support** — Switch between Ollama, Anthropic, OpenAI, and Google models at runtime
- [x] **Conversation Memory** — Remembers earlier messages across turns, interrupts and model switches
- [x] **Local API** — Background HTTP server with streaming replies (loopback-only, token-protected)
- [x] **macOS app** — Tap Control+Option anywhere for a small chat box right at your pointer

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Suman7495/tzara/main/install.sh | sh
```

## Usage


## Uninstallion
`sh install.sh --uninstall` removes the app, login items, server and command. It leaves Ollama, its models and your logs.


## For developers

The one-liner works for you too. To hack on Tzara instead:

```bash
git clone https://github.com/Suman7495/tzara.git && cd tzara
make install                      # exact tested versions, plus dev tools
tzara setup --no-service --no-app # just Ollama and the model
make app                          # build the app yourself (needs the Command Line Tools)
```

Skip the login service while developing: it would hold port 8927 (set `TZARA_SERVER_PORT` to run a second server). `tzara setup` fetches the app matching the installed version, so it needs a published release; `make app` builds your own.

**Releasing:** bump `version` in `pyproject.toml`, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The release workflow checks the tag matches, tests and builds the app, and publishes `Tzara.app.zip` with its checksum.

## Quickstart

```bash
tzara
```
Tzara loads the model (a spinner shows, about 12 s when cold), then you chat. Commands:

| Command | |
|---------|-|
| `/list-models` | Show the known models by provider |
| `/set-model <provider:model>` | Switch model; the conversation carries over. If it can't load, the current one is kept |
| `/current-model` | Show the active model |
| `/new` | Forget the conversation and start again |
| `/help`, `/exit` | |

Models are written `provider:model-name`, e.g. `ollama:qwen3:8b`, `anthropic:claude-haiku-4-5`, `openai:gpt-4o`, `google_genai:gemini-3.5-flash`. Any model LangChain can load works.

## macOS app

A menu-bar app that opens a small chat box beside your mouse, over whatever you're doing, including full-screen apps. It talks to the local server below.

### Launch the app

1. **Open it:** double-click `~/Applications/Tzara.app` (or Spotlight, or `open ~/Applications/Tzara.app`). The chat box appears at once, so you can start without the hotkey. Opening the app again brings the box back. At login it starts quietly.
   - No such file? Setup couldn't download it (it prints `! There is no app build for version …`). Build it: `xcode-select --install` (once), clone the repo, `make app`, then `open app/build/Tzara.app`.
2. **Check the server:** `tzara status` should say `ready`; if not, `tzara start`.
3. **Allow Input Monitoring** for the hotkey: macOS asks on first launch, or switch **Tzara** on in *System Settings → Privacy & Security → Input Monitoring* and reopen the app. Without it only the box and the menu-bar icon work.
4. **Tap Control+Option** (press both, release, touch nothing else) to open or close the box.

- **Tap Control+Option** (press both, release, touch nothing else) to open or close the box. It opens with the input line under your pointer, so you can type straight away; your previous chat is shown. **×** or **Esc** closes it.
- **Pencil**: new conversation. **Gear → Settings**: pick the model from a dropdown (or type any `provider:model-name`), rename, check the hotkey and server, quit.
- **Double-click the name** at the top to rename it. The assistant uses the new name when it describes itself, and remembers it.
- The menu-bar icon opens the chat, shows whether the hotkey is active, and quits.

The hotkey check looks only for the Control+Option tap and doesn't record what you type. Each update or rebuild re-asks for Input Monitoring unless you sign with a stable identity: create a code-signing certificate in Keychain Access (*Certificate Assistant → Create a Certificate*) and run `TZARA_SIGN_IDENTITY="Your Cert" make app`. The app's log is `~/Library/Logs/tzara/app.log`. `make app-test` runs its tests.

## Local server

```bash
tzara start     # run in the background; returns in a couple of seconds while the model loads
tzara status    # loading, ready or failed
tzara stop      # clean, even mid-reply
tzara serve     # or run it in this terminal
```

It listens only on `127.0.0.1`. The server keeps `token` and `server.json` (`{"host", "port", "pid"}`) in `~/Library/Application Support/tzara/` (`~/.local/share/tzara/` on Linux, `%APPDATA%\tzara\` on Windows; `TZARA_DATA_DIR` overrides). Everything below `/v1` except `health` needs `Authorization: Bearer <token>`.

| Endpoint | |
|----------|-|
| `GET /v1/health` | `{"status": "ok"}`; no token |
| `GET /v1/status` | `{"state", "model", "name", "busy", "error"}` |
| `GET /v1/models` | `{"providers": [{"name", "models": [{"id", "description"}]}]}` |
| `GET /v1/conversation` | `{"messages": [{"role": "user"\|"assistant", "text"}]}` |
| `POST /v1/chat` `{"message"}` | Streams the reply as server-sent events |
| `POST /v1/interrupt` | Cancel the reply being written (`204`) |
| `POST /v1/conversation/new` | Forget the conversation |
| `PUT /v1/model` `{"model"}` | Switch model (about 15 s) |
| `PUT /v1/name` `{"name"}` | Rename the assistant, 1–40 characters on one line (about 10 s) |

**Chat events:** `event: token` with `{"text"}` (concatenate them), then `done` with `{"first_token_seconds", "characters"}`, or `error` with `{"message"}`. After 10 s of silence the server sends a `: keep-alive` comment line; ignore it. Closing the connection cancels the reply.

**Status codes:** `409` another reply is being written, `503` the model isn't ready, `413`/`422` rejected input, `502` a model or name change failed (the old one stays), `401` bad token.

**Security:** a local port is reachable by any program and any web page, so requests must carry the token, must not carry a browser `Origin` header (browser clients are refused), and must be addressed to a loopback `Host`.

The conversation lives in the server's memory, until you start a new one or stop the server. It survives interrupts and model switches, and old turns are summarized rather than cut off as it nears the context window.

## Configuration

Defaults live in [`TzaraConfig`](src/tzara/config.py). Override with environment variables:

| Variable | Default | Meaning |
|----------|---------|---------|
| `TZARA_MODEL` | `ollama:qwen3:8b` | Model to start with |
| `TZARA_OLLAMA_NUM_CTX` | `16384` | Context window (Ollama's default of 4096 truncates the prompt) |
| `TZARA_OLLAMA_KEEP_ALIVE` | `30m` | How long Ollama keeps the model loaded while idle; longer avoids reload waits but holds 5-8 GB |
| `TZARA_OLLAMA_REASONING` | `false` | Let thinking models such as Qwen3 think first; slower |
| `TZARA_OLLAMA_RESERVED_TOKENS` | `4096` | Part of the window kept for the prompt and reply; the rest is the history budget |
| `TZARA_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `TZARA_LOG_FILE` | platform default | `~/Library/Logs/tzara/tzara.log`, `~/.local/state/tzara/tzara.log`, or `%LOCALAPPDATA%\tzara\Logs\tzara.log` |
| `TZARA_SERVER_PORT` | `8927` | Server port, on `127.0.0.1` |
| `TZARA_DATA_DIR` | platform default | Where the server keeps its token, address and name |

Logs go to a rotating file, never the chat; errors in the chat say where. They hold startup, model loads and per-turn timing, **not what you or Tzara said** (that needs `DEBUG`).

## Adding Custom Tools

Tools are small `BaseTool` classes with a typed input schema. Create one (`src/tzara/tools/weather.py`):
```python
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class WeatherInput(BaseModel):
    """Arguments accepted by the weather tool."""

    city: str = Field(description="City name, e.g. 'Berlin'.")


class Weather(BaseTool):
    """Tool that reports the current weather for the agent."""

    name: str = "get_weather"
    description: str = "Get current weather for a city."
    args_schema: type[BaseModel] = WeatherInput

    def _run(self, city: str) -> str:  # pylint: disable=arguments-differ
        """Return the weather for ``city`` as text."""
        return f"Sunny in {city}, 72°F"
```
Register it in `Tzara.__init__` (`src/tzara/agent/agent.py`):
```python
self._tools = [Calculator(), Weather()]
```
Tzara decides when to use it. Add a test in `tests/` and run `make check`.

## Project Structure

```
tzara/
├── src/tzara/
│   ├── config.py, prompts.py, logs.py, paths.py   # settings, prompts, logging, file locations
│   ├── agent/            # Deep Agent, conversation memory, model factory
│   ├── cli/              # `tzara` command, chat loop, slash commands
│   ├── server/           # Local HTTP server (FastAPI) and its background manager
│   ├── installer/        # `tzara setup`: Ollama, login service, app download
│   └── tools/            # Calculator, and where custom tools go
├── install.sh            # the one-line installer
├── app/                  # macOS app (Swift): hotkey, chat box, settings
├── tests/                # pytest suite (runs offline)
├── .github/workflows/    # CI
├── CLAUDE.md             # Developer guide
├── pyproject.toml        # Dependency ranges, linter and test configuration
└── requirements.lock     # Exact tested dependency versions
```

## Learn More

- [LangChain DeepAgents](https://docs.langchain.com/oss/python/deepagents/overview) — Agent framework
- [LangGraph](https://python.langchain.com/docs/langgraph/) — Orchestration primitives
- [LangChain Tools](https://python.langchain.com/docs/modules/tools/) — Tool building
- [Model Context Protocol](https://modelcontextprotocol.io/) — Integration standard

## Contributing

Contributions are welcome: new tools, MCP integrations, docs, fixes. Run `make check` (ruff, pylint, mypy, pytest) before opening a pull request; CI runs the same. See [CLAUDE.md](CLAUDE.md) for conventions, the dependency lockfile and git hooks.

## License

[MIT](/LICENSE)
