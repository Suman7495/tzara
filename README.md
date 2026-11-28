<p align="center">
  <img src="assets/tzara_banner.png" alt="Tzara banner" width="100%">
</p>

# Tzara: Your Cute Personal AI Agent

Tzara is an open-source, **local-first personal AI agent** built on **LangChain's agent framework (LangGraph)** and designed for developers who want to learn and experiment with AI agents in Python.

It's fully local, supports multiple models, can search the web, open apps and sites and control your music, and is extensible with custom tools.

**No cloud. Complete data privacy:** the model, your chats and what Tzara remembers about you stay on your machine, in one file only you can read (`memory.db` in Tzara's data folder). The exceptions are web search, which sends the search query out (`TZARA_WEB_SEARCH=false` turns it off). Don't want anything kept? `TZARA_MEMORY=false`.


## Features

- [x] **Lightweight & low footprint** — Small Python core with fast startup and low memory usage, runs comfortably on a personal laptop.
- [x] **Multi-Model Support** — Switch between Ollama, Anthropic, OpenAI, and Google models at runtime
- [x] **Conversation Memory** — Remembers earlier messages across turns, interrupts and model switches
- [x] **Understands what you are looking at** (off until you turn it on in Settings) — Open Tzara over any app and say "translate this" or "explain it": it knows which app and window you are in and the text you had selected. "Summarize this page" or "translate this document" reads the page behind it (public web pages, and text or PDF files you have open) and answers from it; two one-word buttons (Explain, Translate) do the common things. Ask about what you are working on ("reply to this email, saying I will pay Thursday", "what amount is she asking for?") and it also reads the field you are typing in and the text in your window. It leans towards your screen: a question that has nothing to do with it is left to the model to decide, and a question that asks for the web ("search", "news", "weather") is searched at once. A box above the input always shows what Tzara is aware of (the app, the window, your selection), during and after replies; it goes with every message, you can remove it, and nothing is read from password managers or password fields. macOS asks for the Accessibility permission once
- [x] **Connects to your apps (MCP)** — Add apps in Settings → Apps with one press (Microsoft 365: Outlook and Calendar, OneDrive files, Teams) or any MCP server of your own ("Other app…"). Tzara can then use their tools ("what is on my calendar tomorrow?"). Only read-only tools are offered unless you allow more, and what an app returns is treated as data, never as instructions. Needs Node.js for most apps. Sign in to Microsoft 365 by asking Tzara to "sign in to Microsoft 365": it gives you a link and a code
- [x] **Web search and research** — For a question that asks for facts (how to fix something, health, news, comparisons, what to buy) it searches the web, reads the best pages with a proper article extractor (`trafilatura`; Stack Overflow through its public API), prefers official, reference and documentation sources, copies code from the docs, and answers from that with its sources. It uses no extra model request for this, so it stays quick, and it says what it is doing while it works (on by default; DuckDuckGo)
- [x] **Open things and control music** — Opens apps (including Terminal), websites, files and folders, and plays, pauses or skips music and sets the volume (macOS; it cannot run commands)
- [x] **Memory** — Remembers lasting things about you (your name, preferences, how you like answers) and picks the conversation up again after a restart. Ask it to remember or forget something; see and delete everything in Settings
- [x] **Rich replies** — Formatted answers (bold, lists, headings, code) with clickable links, a **Copy** button under every message, and follow-up buttons you can click to ask the next question
- [x] **Local API** — Background HTTP server with streaming replies (loopback-only, token-protected)
- [x] **macOS app** — Tap Right Option in any text field for a small bar: say what to write (an email, a reply, a rewrite) and it is written there

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Suman7495/tzara/main/install.sh | sh
```

## Usage

1. **Open Tzara:** double-click `~/Applications/Tzara.app` (or use Spotlight). A small chat box appears.
2. **Allow Input Monitoring** when macOS asks (or in *System Settings → Privacy & Security → Input Monitoring*), then quit and reopen Tzara.
3. **Tap Right Option** in any text field (nothing else pressed) to open the small bar, say what to write, press Enter. Nothing is written until you ask, and nothing is sent.

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
| `/memory` | Show what Tzara remembers about you |
| `/erase-memory yes` | Forget everything: all facts and all stored conversations |
| `/help`, `/exit` | |

Models are written `provider:model-name`, e.g. `ollama:qwen3:8b`, `anthropic:claude-haiku-4-5`, `openai:gpt-4o`, `google_genai:gemini-3.5-flash`. Any model LangChain can load works.

## macOS app

A menu-bar app that opens a small chat box beside your mouse, over whatever you're doing, including full-screen apps. It talks to the local server below.

- **Tap Right Option** opens the small bar next to your text field; **Esc** closes it. Ask a question and it answers in the bar; drag it to move it; it hides after 3 seconds of no interaction (pin it to keep it). The **dashboard** (conversation, settings, memory, apps) opens from the menu-bar icon.
- **Pencil**: new conversation. **Gear → Settings**: pick the model from a dropdown (or type any `provider:model-name`), rename, see and delete what Tzara remembers, check the hotkey and server, **Update** to the latest release, **Uninstall Tzara**.
- **Replies are formatted** (bold, lists, headings, code) and links open in your browser. The **copy** icon in the top-right corner of a message copies its text. While Tzara works it says what it is doing ("Searching the web for …", "Reading 5 pages…") instead of only spinning. Closing the box (Esc or the − button) while a reply is being written lets it finish in the background; the menu-bar icon shows when it is ready.
- **Links.** Any link in a reply is one Tzara really got from a search or a page it read that turn, or one you wrote yourself. Anything else it writes is taken out before you see it, so it cannot make a product page up.
- **Opening the box wakes the model.** If Tzara has been idle for a couple of minutes, opening the chat box starts loading the model, so it is ready by the time you have typed.
- **Memory.** Say "remember that I prefer short answers" or "forget where I live". Tzara keeps lasting facts about you and tells the model the relevant ones before each answer; it also restores your last conversation when it restarts and can search older ones ("what did we say about the Alps trip?"). **Settings → Memory** lists every fact with a trash icon, and **Erase all memory…** forgets everything. Only what *you* say can be remembered: after Tzara has read a web page in a turn, it refuses to remember or forget anything, so a page cannot plant facts. A small model can occasionally say it remembered or forgot something without doing it, so check Settings.
- **Double-click the name** at the top to rename it. The assistant uses the new name when it describes itself, and remembers it.
- **Update automatically** (on by default) and **Update** look up the newest release on GitHub and install it if it is newer. Progress and errors go to `~/Library/Logs/tzara/update.log`.
- The menu-bar icon opens the chat, shows whether the hotkey is active, and has **Quit Tzara**.

**If the app is missing or the hotkey does nothing:**
- No `~/Applications/Tzara.app`: setup couldn't download it (it printed `! There is no app build for version …`). Build it with `xcode-select --install` (once), clone the repo, `make app`, then `open app/build/Tzara.app`.
- `tzara status` should say `ready`; if not, `tzara start`.
- Without Input Monitoring only the box and the menu-bar icon work. Switch **Tzara** on in *System Settings → Privacy & Security → Input Monitoring*, then quit and reopen it. The log line `watching for Control+Option (Input Monitoring granted: true)` confirms it.

The hotkey check looks only for the Right Option tap and doesn't record what you type. Releases are signed with one fixed certificate, so updating keeps the permission (a build you make yourself is signed ad-hoc, and asks again, unless you run `app/scripts/create_signing_identity.sh` once). The app's log is `~/Library/Logs/tzara/app.log`. `make app-test` runs its tests.

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
| `POST /v1/wake` | Get an idle model ready (`202` at once, `{"waking": true\|false}`); the app calls it when the box opens |
| `GET /v1/memory` | `{"enabled", "facts": [{"id", "text", "created"}], "conversations"}`: what Tzara remembers about you |
| `DELETE /v1/memory/facts/{id}` | Forget one fact (`204`, or `404`) |
| `DELETE /v1/memory` | Forget everything: all facts, all stored conversations, and the current one (`204`; `409` while a reply is being written) |
| `GET /v1/conversation` | `{"messages": [{"role": "user"\|"assistant", "text"}]}` |
| `POST /v1/chat` `{"message"}` | Streams the reply as server-sent events |
| `POST /v1/interrupt` | Cancel the reply being written (`204`) |
| `POST /v1/conversation/new` | Forget the conversation |
| `PUT /v1/model` `{"model"}` | Switch model (about 15 s) |
| `PUT /v1/name` `{"name"}` | Rename the assistant, 1–40 characters on one line (about 10 s) |

**Chat events:** `event: token` with `{"text"}` (concatenate them), `event: status` with `{"text"}` (what the agent is doing now, such as "Reading bbc.com…"; show it while waiting, it is not part of the reply), `event: tool` with `{"name", "state"}` (`started`, `completed` or `failed`, for a client that tracks which tool is running), then `done` with `{"first_token_seconds", "characters"}`, or `error` with `{"message"}`. After 10 s of silence the server sends a `: keep-alive` comment line; ignore it. Closing the connection cancels the reply.

**Status codes:** `409` another reply is being written, `503` the model isn't ready, `413`/`422` rejected input, `502` a model or name change failed (the old one stays), `401` bad token.

**Security:** a local port is reachable by any program and any web page, so requests must carry the token, must not carry a browser `Origin` header (browser clients are refused), and must be addressed to a loopback `Host`.

The conversation survives interrupts and model switches, and old turns are summarized rather than cut off as it nears the context window. What was said is also written to `memory.db`, so after a restart the server picks the conversation up where it left off; `POST /v1/conversation/new` ends it.

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
| `TZARA_SCREEN` | `true` | Let the server accept the app, window and selected text the Mac app sends. The app only sends them if you turn the setting on; set `false` to ignore them |
| `TZARA_SYSTEM_TOOLS` | `true` | Let Tzara open apps, sites, files and folders and control music (macOS only). It never runs commands or opens scripts and installers; set `false` to turn it off |
| `TZARA_MEMORY` | `true` | Keep facts about you and your conversations on disk, in `memory.db` in the data folder (readable only by you). Set `false` and Tzara forgets everything when it stops |
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
self._tools = [*WebTools.create(self._config), Weather()]
```
Tzara decides when to use it. Add a test in `tests/` and run `make check`.

## For developers

The one-liner works for you too. To hack on Tzara instead:

```bash
git clone https://github.com/Suman7495/tzara.git && cd tzara
make install                      # exact tested versions, plus dev tools
tzara setup --no-service --no-app # just Ollama and the model
make app                          # build the app yourself (needs the Command Line Tools)
make dev                          # try your changes: server + app from this checkout
```

**Trying your changes:** `make dev` builds the app, starts a server on its own port (18927) with its own data folder (`/tmp/tzara-dev`), and opens the app against it. The installed server, token and name are untouched. It quits the installed app while it runs, because both would answer Control+Option, and reopens it when you quit the dev app (menu-bar icon) or press Ctrl+C. Needs the project's environment (`conda activate tzara && make install`, once). `DEV_KEEP_INSTALLED=1 make dev` leaves the installed app running.

Skip the login service while developing: it would hold port 8927 (set `TZARA_SERVER_PORT` to run a second server). `tzara setup` fetches the app matching the installed version, so it needs a published release; `make app` builds your own.

**Checks (CI).** Every push to `main` and every pull request runs `make check` (ruff, mypy, pytest) on Python 3.11 and 3.12, confirms `requirements.lock` matches `pyproject.toml`, and runs the macOS app's tests. A weekly job tries the newest dependency versions the ranges allow, so upstream breakage shows up early. Run `make check` yourself before pushing.

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
│   ├── agent/            # the agent, conversation memory, model factory
│   ├── cli/              # `tzara` command, chat loop, slash commands
│   ├── server/           # Local HTTP server (FastAPI) and its background manager
│   ├── installer/        # `tzara setup`: Ollama, login service, app download
│   └── tools/            # web search and research, memory, Mac tools (open, media), and where custom tools go
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

- [LangChain agents](https://docs.langchain.com/oss/python/langchain/agents) — Agent framework
- [LangGraph](https://python.langchain.com/docs/langgraph/) — Orchestration primitives
- [LangChain Tools](https://python.langchain.com/docs/modules/tools/) — Tool building
- [Model Context Protocol](https://modelcontextprotocol.io/) — Integration standard

## Contributing

Contributions are welcome: new tools, MCP integrations, docs, fixes. Run `make check` (ruff, mypy, pytest) before opening a pull request; CI runs the same. See [CLAUDE.md](CLAUDE.md) for conventions, the dependency lockfile and git hooks.

## License

[MIT](/LICENSE)
