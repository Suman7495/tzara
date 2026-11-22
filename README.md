<p align="center">
  <img src="assets/tzara_banner.png" alt="Tzara banner" width="100%">
</p>

# Tzara: Your Cute Personal AI Agent

Tzara is an open-source, **local-first personal AI agent** built on **LangChain's DeepAgents framework** and designed for developers who want to learn and experiment with AI agents in Python.

It's fully local, supports multiple models, can search the web, open apps and sites and control your music, and is extensible with custom tools.

**No cloud. Complete data privacy:** the model and your chats stay on your machine. The one exception is web search, which sends the search query out; turn it off with `TZARA_WEB_SEARCH=false`.


## Features

- [x] **Lightweight & low footprint** — Small Python core with fast startup and low memory usage, runs comfortably on a personal laptop
- [x] **Multi-Model Support** — Switch between Ollama, Anthropic, OpenAI, and Google models at runtime
- [x] **Conversation Memory** — Remembers earlier messages across turns, interrupts and model switches
- [x] **Web search** — Searches the web and reads pages, then gives a short summary with its source (on by default; DuckDuckGo, or Google through Gemini)
- [x] **Open things and control music** — Opens apps (including Terminal), websites, files and folders, and plays, pauses or skips music and sets the volume (macOS; it cannot run commands)
- [x] **Local API** — Background HTTP server with streaming replies (loopback-only, token-protected)
- [x] **macOS app** — Tap Control+Option anywhere for a small chat box right at your pointer

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Suman7495/tzara/main/install.sh | sh
```

## Usage

1. **Open Tzara:** double-click `~/Applications/Tzara.app` (or use Spotlight). A small chat box appears.
2. **Allow Input Monitoring** when macOS asks (or in *System Settings → Privacy & Security → Input Monitoring*), then quit and reopen Tzara.
3. **Tap Control+Option** anywhere (press both, release, touch nothing else) to open or close the box at your pointer.

Not working? `tzara status` should say `ready`, and the log is `~/Library/Logs/tzara/app.log`. More in [macOS app](#macos-app). Prefer a terminal? Run `tzara` ([command line](#command-line)).

## Update

Tzara updates itself by default: every few hours it checks GitHub and installs a newer release when the chat box is closed, then restarts (about a minute). Turn that off, or update right away, in **gear → Settings** (*Update automatically*, *Update*). Or rerun the install command.

## Uninstall

In the app: **gear → Settings → Uninstall Tzara** (asks to confirm), or run `sh install.sh --uninstall`. Either removes the app, login items, server and command. It leaves Ollama, its models and your logs.

## Command line

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

- **Tap Control+Option** opens or closes the box. It opens with the input line under your pointer, so you can type straight away; your previous chat is shown. **×** or **Esc** closes it.
- **Pencil**: new conversation. **Gear → Settings**: pick the model from a dropdown (or type any `provider:model-name`), rename, check the hotkey and server, **Update** to the latest release, **Uninstall Tzara**.
- **Double-click the name** at the top to rename it. The assistant uses the new name when it describes itself, and remembers it.
- **Update automatically** (on by default) and **Update** look up the newest release on GitHub and install it if it is newer. Progress and errors go to `~/Library/Logs/tzara/update.log`.
- The menu-bar icon opens the chat, shows whether the hotkey is active, and has **Quit Tzara**.

**If the app is missing or the hotkey does nothing:**
- No `~/Applications/Tzara.app`: setup couldn't download it (it printed `! There is no app build for version …`). Build it with `xcode-select --install` (once), clone the repo, `make app`, then `open app/build/Tzara.app`.
- `tzara status` should say `ready`; if not, `tzara start`.
- Without Input Monitoring only the box and the menu-bar icon work. Switch **Tzara** on in *System Settings → Privacy & Security → Input Monitoring*, then quit and reopen it. The log line `watching for Control+Option (Input Monitoring granted: true)` confirms it.

The hotkey check looks only for the Control+Option tap and doesn't record what you type. Releases are signed with one fixed certificate, so updating keeps the permission (a build you make yourself is signed ad-hoc, and asks again, unless you run `app/scripts/create_signing_identity.sh` once). The app's log is `~/Library/Logs/tzara/app.log`. `make app-test` runs its tests.

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
| `TZARA_WEB_SEARCH` | `true` | Let Tzara search the web and read pages. This is the one feature that sends something off your machine (the search query, and page requests); set `false` to keep everything local |
| `TZARA_WEB_SEARCH_PROVIDER` | `duckduckgo` | `duckduckgo` needs nothing. `gemini` searches Google through the Gemini API and needs `GOOGLE_API_KEY` |
| `TZARA_SYSTEM_TOOLS` | `true` | Let Tzara open apps, sites, files and folders and control music (macOS only). It never runs commands or opens scripts and installers; set `false` to turn it off |
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
self._tools = [Calculator(), *WebTools.create(self._config), Weather()]
```
Tzara decides when to use it. Add a test in `tests/` and run `make check`.

## For developers

The one-liner works for you too. To hack on Tzara instead:

```bash
git clone https://github.com/Suman7495/tzara.git && cd tzara
make install                      # exact tested versions, plus dev tools
tzara setup --no-service --no-app # just Ollama and the model
make app                          # build the app yourself (needs the Command Line Tools)
```

Skip the login service while developing: it would hold port 8927 (set `TZARA_SERVER_PORT` to run a second server). `tzara setup` fetches the app matching the installed version, so it needs a published release; `make app` builds your own.

**Checks (CI).** Every push to `main` and every pull request runs `make check` (ruff, pylint, mypy, pytest with coverage) on Python 3.11 and 3.12, confirms `requirements.lock` matches `pyproject.toml`, and runs the macOS app's tests. A weekly job tries the newest dependency versions the ranges allow, so upstream breakage shows up early. Run `make check` yourself before pushing.

**Releasing.** Once: run `app/scripts/create_signing_identity.sh` and save the two GitHub secrets it prints (`TZARA_SIGNING_P12`, `TZARA_SIGNING_PASSWORD`). Every app is then signed with the same certificate, so users keep their Input Monitoring permission across updates; the workflow refuses to publish an unsigned app.

**To release, merge `develop` into `main`. There is nothing else to do.** The merge starts the release workflow, which picks the next version (the latest release plus one patch, so `0.1.2` becomes `0.1.3`), runs `make check` and the app tests, builds and signs the app, writes the version into `pyproject.toml`, commits that to `main` and publishes `Tzara.app.zip` with its checksum under a new `v0.1.3` tag. Installed copies then update themselves within a few hours. Follow it under the repo's *Actions* tab.

- **Bigger steps:** put `[minor]` or `[major]` in any commit message in the merge (`0.1.9` → `0.2.0`, `0.9.0` → `1.0.0`). Or run the workflow by hand from the *Actions* tab and choose the step.
- **No release for non-product changes:** a merge that only touches docs, tests or CI is not released. Anything under `src/` or `app/`, or `install.sh`, `pyproject.toml` or `requirements.lock`, is.
- **Retry:** if a release fails (checks, build or signing), nothing is published and `main` is left as it was; fix it and merge again, or rerun the workflow from the *Actions* tab.
- **`develop` catches up by itself:** after every merge to `main` (released or not), the workflow merges `main` into `develop`, so it always has the release commit. Commits pushed to `develop` in the meantime are kept. Locally, just `git pull` before you push, as always. If `develop` and `main` ever conflict, that step fails with the commands to fix it by hand. Don't edit the `version` line in `pyproject.toml` yourself: it is the one line both sides change.
- **Protected branches:** the workflow pushes the version commit to `main` and the catch-up merge to `develop`, so if you require pull requests on either, let the GitHub Actions bot bypass that rule.

Running `make app` after the signing script signs your local builds with the same certificate.

## Project Structure

```
tzara/
├── src/tzara/
│   ├── config.py, prompts.py, logs.py, paths.py   # settings, prompts, logging, file locations
│   ├── agent/            # Deep Agent, conversation memory, model factory
│   ├── cli/              # `tzara` command, chat loop, slash commands
│   ├── server/           # Local HTTP server (FastAPI) and its background manager
│   ├── installer/        # `tzara setup`: Ollama, login service, app download
│   └── tools/            # Calculator, web search, Mac tools (open, media), and where custom tools go
├── install.sh            # the one-line installer
├── scripts/              # helpers the release workflow uses (next version, set version)
├── app/                  # macOS app (Swift): hotkey, chat box, settings
├── tests/                # pytest suite (runs offline)
├── .github/workflows/    # CI (every push/PR), weekly dependency check, release (on a merge to `main`)
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
