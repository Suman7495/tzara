# Tzara Deep Agent

Tzara is a local-first personal AI agent built on LangChain's **DeepAgents** framework.

## Architecture

### Core Stack
- **LLM**: Ollama (default: `qwen3:8b`)
- **Agent Framework**: LangChain's DeepAgents
- **Runtime**: LangGraph (underlying engine)
- **Tools**: LangChain `BaseTool` subclasses (see Adding Tools)

### Key Components

**1. Execution Environment**
- Tool execution: Calculator and custom tools
- File system access and management (via DeepAgents)
- Code execution sandbox (built-in)

**2. Context Management**
- System prompt defining behavior rules
- Automatic context summarization for long conversations
- Prompt caching for efficiency

**3. Delegation** (Future)
- Subagent spawning for isolated subtasks
- Task planning and parallelization
- Fresh context windows for specialized work

**4. Human Steering** (Future)
- Interrupt gates for sensitive operations: `interrupt_on={"edit_file": True}`
- Filesystem permission rules
- Human approval workflows

## Project Layout

```
src/tzara/
├── config.py        # TzaraConfig: every tunable value, overridable via TZARA_* env vars
├── prompts.py       # Prompts: system prompt and tool descriptions sent to the model
├── logs.py          # LogConfigurator, LogLocation: rotating log file, never the chat
├── agent/
│   ├── agent.py     # Tzara: stream a reply, warm up the model, cancel a response
│   ├── harness.py   # HarnessConfigurator: applies prompts to the DeepAgents harness
│   ├── memory.py    # ConversationMemory (history that survives interrupts and switches), HistoryMessage
│   ├── models.py    # ModelCatalog (known models) and ModelFactory (builds chat models)
│   └── summaries.py # SummaryRunFilter: keeps the summarizer's output out of the reply
├── paths.py         # DataLocation: where the token and server files live
├── cli/
│   ├── launcher.py  # Launcher: the `tzara` command (chat, serve, start, stop, status)
│   ├── run.py       # TzaraCLI: chat loop, Ctrl+C handling
│   └── commands.py  # CommandRegistry and BuiltinCommands (slash commands)
├── server/
│   ├── app.py       # TzaraServer (FastAPI app, guards, SSE), ShutdownAwareServer
│   ├── service.py   # ChatService, ChatTurn: one conversation, one reply at a time
│   ├── files.py     # TokenStore, ServerInfoFile, NameStore: what clients read, and what is remembered
│   ├── daemon.py    # DaemonManager: `tzara start/stop/status`
│   └── client.py    # ServerClient: health and status checks
├── installer/       # `tzara setup`/`uninstall`: OllamaSetup, AppInstaller, ServiceInstaller/LaunchAgent, Installer
└── tools/
    └── calculator.py  # Calculator tool and the safe ExpressionEvaluator behind it
app/                 # macOS app (Swift package): hotkey, chat box, settings; see "macOS App" below
tests/               # pytest suite; helpers.py holds the fake console, agent and models
requirements.lock    # exact tested versions of every dependency
.github/workflows/   # CI: checks on every push/PR, weekly check against the newest dependencies
```

## Code Conventions

- **Classes, not loose functions.** Behaviour lives in a class. There are no module-level
  helper functions; the only entry points are `Launcher.main` and `python -m tzara`.
- **No hard-coded values in logic.** Tunables go in `config.py` and prompt text in
  `prompts.py`, so tests and users can override them.
- **Google-style docstrings** on every public module, class and method (enforced by ruff).
- **Type hints everywhere**; `mypy` runs with `disallow_untyped_defs` on `src/tzara`.

## Development

```bash
make install              # install the exact versions in requirements.lock, plus Tzara
make check                # ruff + pylint + mypy + pytest with coverage (CI runs this)
make app-test             # the macOS app's tests (needs the Command Line Tools)
make format               # auto-format and fix import order
pre-commit install --hook-type pre-commit --hook-type pre-push   # optional, see below
```

`make check` must pass before committing: pylint stays at 10.00/10 and coverage stays
above 95% (currently 100%).

**Dependencies.** `pyproject.toml` allows a range for each package. The packages whose
pre-1.0 or experimental APIs Tzara uses (DeepAgents middleware and harness profiles,
LangGraph's v3 streaming) are capped at the next minor release. `requirements.lock` pins
the exact versions that were tested, for Linux, macOS and Python 3.11+. When you change
dependencies in `pyproject.toml`, run `make lock`; `make upgrade` moves everything to the
newest allowed versions (run `make check` afterwards). `make install-latest` installs
without the lock.

**CI** ([.github/workflows](.github/workflows)):
- `ci.yml` runs `make check` on Python 3.11 and 3.12 using the lockfile, and fails if
  `requirements.lock` is out of date with `pyproject.toml`.
- `upstream-canary.yml` runs weekly against the newest releases `pyproject.toml` allows,
  so breakage in the experimental APIs shows up before anyone upgrades the lock.

**Git hooks** (`.pre-commit-config.yaml`): ruff runs on every commit from its own pinned
environment. pylint, mypy and pytest run before a push and need the project's environment
to be active. Remove the hooks with
`pre-commit uninstall --hook-type pre-commit --hook-type pre-push`.

Tests run offline: `tests/helpers.py` provides a scripted console, a fake agent for CLI
tests, and fake chat models that record the tools and prompt DeepAgents sends them. A
test caps the total tool-description size, because a larger prompt makes the first
message after loading the model slower (a local model evaluates ~250 tokens/s).

## Conversation Memory

`ConversationMemory` ([memory.py](src/tzara/agent/memory.py)) holds a LangGraph checkpointer
and a thread id. The CLI creates one and gives it to every agent it builds, so the history,
and files the agent wrote, survive Ctrl+C and `/set-model`. `/new` starts a fresh thread.
The warm-up message runs in a throwaway thread so it never appears in the history.

History is kept in memory only: it ends with the process. Nothing is written to disk.

Ollama models report no context size, so DeepAgents would only summarize old turns at
170,000 tokens, far past our 16k window, and Ollama would silently truncate the prompt.
`ModelFactory` therefore gives Ollama models a profile whose `max_input_tokens` is
`num_ctx - reserved_tokens` (the room left for history), which makes DeepAgents summarize
when history nears that budget. The summarizer calls the same model mid-turn and its
output is streamed like any other, so `SummaryRunFilter` recognises those runs and
`Tzara.handle` skips them.

## Server

`tzara serve` runs a FastAPI server (`tzara start` runs it in the background, with
`stop` and `status`). It is the one place the agent runs for other programs; the
macOS app is a thin client of it. The endpoints are in the [README](README.md#local-server).

- **Security.** A local port is reachable by any program and any web page, so every request
  must carry the secret token (a `0600` file, created owner-only from the start), must not
  carry a browser `Origin` header, and must be addressed to a loopback `Host`. It only ever
  listens on `127.0.0.1`; that is a class constant, not a setting. Interactive docs are off.
- **One reply at a time.** `ChatService` refuses extra requests (`409`) and requests made
  while the model loads (`503`) instead of queueing them, so a client can show the state.
- **Replies run on a worker thread** (`ChatTurn`) that pushes events into the request's
  queue. Cancelling, or a client disconnecting mid-reply, therefore never has to close a
  generator that is still running; it closes the model's connection and the thread finishes.
- **Clean stop.** `ShutdownAwareServer` cancels the reply the moment the stop signal arrives,
  because uvicorn otherwise waits for open streams (`tzara stop` took 5 s mid-reply; now
  under half a second). `tzara stop` also refuses to signal a process that does not answer
  as a Tzara server, since a stale record's pid may belong to an unrelated program.
- **The assistant's name** is part of the system prompt (`Prompts.assistant_name`), so renaming
  rebuilds and reloads the agent (`ChatService.rename`, shared with model switching in
  `_replace_agent`: on any failure the old agent stays). `NameStore` saves it in the data
  directory, so the server is the single source of truth and a restart keeps the name.
- **The conversation** is read back with `Tzara.history()` (user and assistant text only), so a
  client that reopens, or restarts, can show the old chat.
- **Keep-alives.** A reply that is silent for `heartbeat_seconds` gets an SSE comment line, so
  clients with a read timeout (Swift's `URLSession` defaults to 60 s) keep the connection.

## macOS App

`app/` is a Swift package: `TzaraCore` (everything that needs no screen, unit-tested with
Swift Testing), `TzaraApp` (AppKit and SwiftUI), and `TzaraChatProbe` (streams one reply or
runs `status`, `models`, `history`, `model`, `rename` from a terminal, to check the client
against a real server). `make app` builds `app/build/Tzara.app`; `make app-test` runs the tests.

- **The hotkey is a tap, not a press.** `ChordDetector` fires when Control+Option are
  *released* with nothing else touched, so shortcuts that start with them (window managers use
  Control+Option+Arrow) never open the box. It is a listen-only `CGEventTap`, so it never
  alters keystrokes; macOS needs Input Monitoring for that, and the monitor polls until granted.
- **Placement.** `PanelPlacement` opens the box with its input line under the pointer
  (pointer offset 28, 22 from the box's corner; `ChatView.inputLine` must match the layout),
  mirrors it near the right edge, and pushes it inside the screen's usable area.
- **A non-activating panel** (`ChatBoxPanel`) that joins all Spaces and is a full-screen
  auxiliary window, so it floats over full-screen apps without taking focus or switching
  Space; it still takes typing (verified with a decoy app in front).
- **The server is the source of truth** for the conversation, model and name; the app asks on
  each open. `ChatClient` never sends an `Origin` header (the server refuses browser requests)
  and reads bytes itself, because `URLSession`'s line reader drops the blank lines SSE needs.
- `build_app.sh` refuses to produce a bundle whose executable doesn't match its Info.plist.
- **Seeing the UI without Screen Recording permission.** Opening `Tzara` shows the box (the login job passes `--background` to skip that),
  `--settings` opens it on the settings screen, and `--snapshot FILE.png` draws the view into a
  PNG after it has loaded, then quits (the view draws itself, so nothing is captured from the
  screen). Run against a test server (`TZARA_SERVER_PORT`, `TZARA_DATA_DIR`) so a real one is
  left alone. This is how layout problems, like content too tall for the box, show up.
- **Toolchain.** The Command Line Tools are enough (no Xcode). Updating them in place once left
  stale module maps that broke every AppKit build; a clean reinstall fixed it.

## Installer and releases

`install.sh` (POSIX sh, no sudo) installs uv, then Tzara with `uv tool install`, then runs
`tzara setup` ([installer/](src/tzara/installer/)): Ollama and model, a launchd job for the
server (`KeepAlive: {SuccessfulExit: false}`), and the app downloaded from the GitHub release
whose tag equals the package version (sha256 checked). `Installer` talks to Protocol ports, so
tests use fakes and never touch the machine. A missing release is a warning, not a failure
(`AppNotPublished`). `ShutdownAwareServer.handle_exit` must let the process exit with status 0:
uvicorn re-raises the signal, which launchd would count as a crash and restart.
Release: bump `version`, tag `vX.Y.Z`, push; `.github/workflows/release.yml` runs `make app-release`.
Never run `tzara setup`/`uninstall` against default paths while developing: they stop the
running server and app. Use `TZARA_DATA_DIR`, `TZARA_APP_DIR`, `TZARA_SERVER_PORT`.

## Logging

Logs go to a rotating file, never the terminal (the console is the chat). The default
location is `~/Library/Logs/tzara/tzara.log` on macOS, `$XDG_STATE_HOME/tzara/tzara.log`
(`~/.local/state/tzara/`) on Linux, and `%LOCALAPPDATA%\tzara\Logs` on Windows. When an
error appears in the chat it says where the log is, and the traceback is in the file.

At the default `INFO` level the log records startup, model loads, switches, interrupts, and
per-turn timing (first token, total, reply size). **It does not record what you or Tzara
said.** Message text is only logged at `DEBUG`. Library warnings go to the file as well,
and third-party loggers are held at `WARNING`.

## Adding Tools

Tools are `BaseTool` subclasses. Put the logic in a plain class so it can be tested alone,
and keep the tool a thin wrapper that returns errors as text for the model to read:

```python
# src/tzara/tools/weather.py
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
        return f"Sunny in {city}"
```

Then register it in `Tzara.__init__` (`src/tzara/agent/agent.py`) and add tests:
```python
self._tools = [Calculator(), Weather()]
```

## Configuration

Defaults live in `TzaraConfig` ([config.py](src/tzara/config.py)). Override them from the
environment:

| Variable | Default | Meaning |
|----------|---------|---------|
| `TZARA_MODEL` | `ollama:qwen3:8b` | Model, as `provider:model-name` |
| `TZARA_OLLAMA_NUM_CTX` | `16384` | Context window. Ollama's default of 4096 truncates the prompt |
| `TZARA_OLLAMA_KEEP_ALIVE` | `30m` | How long Ollama keeps the model loaded while idle |
| `TZARA_OLLAMA_REASONING` | `false` | Let thinking models (Qwen3) think before answering; slower |
| `TZARA_OLLAMA_RESERVED_TOKENS` | `4096` | Part of the window kept for the system prompt, tool schemas and the reply; the rest is the history budget |
| `TZARA_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. `DEBUG` also logs message text |
| `TZARA_LOG_FILE` | platform default | Log file path |
| `TZARA_SERVER_PORT` | `8927` | Port of the background server (always on `127.0.0.1`) |
| `TZARA_DATA_DIR` | platform default | Directory for the server's token and address file |

In Python, pass a config: `Tzara(config=TzaraConfig(model="ollama:mistral"))`. To continue
an existing conversation with another agent, pass its memory: `Tzara(memory=agent.memory)`.

- **System prompt and tool descriptions**: `Prompts` in [prompts.py](src/tzara/prompts.py).
  DeepAgents' default tool descriptions are replaced with short ones; every tool stays on.
- **Interrupts**: `interrupt_on` in `Tzara._create_agent()`.

## Running Tzara

### CLI Commands

Start the CLI:
```bash
tzara
```

Startup shows a `loading model...` spinner while Ollama loads the model and evaluates the
prompt (about 12s cold, instant when the model is still loaded). Ctrl+C during a reply
cancels that reply; at the prompt it exits.

**Available Commands:**

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/list-models` | Show all available models by provider |
| `/current-model` | Display currently active model |
| `/set-model <model>` | Switch model. The new model is loaded first; if that fails the current one is kept. The conversation carries over |
| `/new` | Forget the conversation so far and start a new one |
| `/exit` | Exit Tzara |

### Model Configuration

Models use the format: `provider:model-name`

**Supported Providers:**
- `ollama`: Local models via Ollama (default)
- `anthropic`: Claude family models
- `openai`: GPT family models
- `google_genai`: Gemini models

**Examples:**
```bash
ollama:qwen3:8b             # Local Qwen3 (default)
ollama:llama3.1:8b          # Local Llama
anthropic:claude-opus-4-8   # Claude Opus
openai:gpt-4o               # GPT-4o
google_genai:gemini-3.5-flash  # Gemini Flash
```

### Python Usage

```python
from tzara.agent import Tzara

# With default model (ollama:qwen3:8b)
agent = Tzara()

# With specific model
agent = Tzara(model="anthropic:claude-opus-4-8")

# Handle user input; the agent remembers earlier messages
response = agent.handle("what is 5 + 3?")
for token in response:
    print(token, end="", flush=True)
```

## References
- [LangChain DeepAgents Docs](https://docs.langchain.com/oss/python/deepagents/overview)
- [LangGraph Concepts](https://python.langchain.com/docs/langgraph/)
- [Tool Building](https://python.langchain.com/docs/modules/tools/)