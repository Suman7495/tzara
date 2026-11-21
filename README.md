<p align="center">
  <img src="assets/tzara_banner.png" alt="Tzara banner" width="100%">
</p>

# Tzara: Your Cute Personal AI Agent

Tzara is an open-source, **local-first personal AI agent** built on **LangChain's DeepAgents framework** and designed for developers who want to learn and experiment with AI agents in Python.

It's fully local, supports multiple models, comes with a calculator tool, and is extensible with custom tools.

**No cloud. Complete data privacy.**


## Features

### Core
- [x] **Lightweight & low footprint** — Small Python core with fast startup and low memory usage, runs comfortably on a personal laptop
- [x] **Multi-Model Support** — Switch between Ollama, Anthropic, OpenAI, and Google models at runtime
- [x] **Conversation Memory** — Remembers earlier messages across turns, interrupts and model switches

## Prerequisites

### For Local Models (Recommended)
Download and install [Ollama](https://ollama.com/download) for your platform (macOS, Linux, WSL, Windows).

Pull a model:
```bash
ollama pull qwen3:8b  # Default model for Tzara (about 5 GB)
```

`qwen3:8b` calls tools reliably and runs well on a 16 GB laptop. `llama3.1:8b` also works but
is noticeably worse at tool calling.

Verify installation:
```bash
ollama list
```

### For Cloud Models (Optional)
Set API keys in your environment:
```bash
export ANTHROPIC_API_KEY=your_key       # For Claude models
export OPENAI_API_KEY=your_key          # For GPT models
export GOOGLE_API_KEY=your_key          # For Gemini models
```

## Installation

Clone the repository:
```bash
git clone https://github.com/Suman7495/tzara.git
cd tzara
```

Install Tzara in development mode, with the exact dependency versions that were tested
(includes the linters and test tools):
```bash
make install
```

Or, with the newest versions `pyproject.toml` allows:
```bash
pip install -e .
```

## Quickstart

Start Tzara:
```bash
tzara
```

Tzara first loads the model, which shows a `loading model...` spinner. Once the model is ready you'll see:
```
Tzara is online. Type /help for commands or start chatting.

You: 
```

Start chatting!

### Commands
You can use the following commands in the chat:
- [x] `/help` — Show available commands
- [x] `/list-models` — Browse all supported models
- [x] `/set-model <model>` — Switch models at runtime
- [x] `/new` — Start a new conversation
- [x] `/current-model` — Check active model
- [x] `/exit` — Exit gracefully


### Available Models

Tzara lists 13 models across 4 providers, and accepts any `provider:model-name` that
LangChain can load:

**Ollama** (local, no API key needed):
- `ollama:qwen3:8b` — Default, fast, reliable tool calling
- `ollama:llama3.1:8b` — Works, weaker tool calling
- `ollama:llama3.1:70b` — Most capable, requires 40GB+ RAM
- `ollama:mistral` — Efficient alternative

**Anthropic Claude**:
- `anthropic:claude-opus-4-8` — Most capable
- `anthropic:claude-opus-4-7`
- `anthropic:claude-sonnet-4-6` — Balanced
- `anthropic:claude-haiku-4-5` — Fast & cheap

**OpenAI**:
- `openai:gpt-4o` — Advanced reasoning
- `openai:gpt-4-turbo`
- `openai:gpt-3.5-turbo` — Fast

**Google Gemini**:
- `google_genai:gemini-3.5-flash` — Fast
- `google_genai:gemini-3.1-pro` — Powerful

### Switching Models at Runtime

Use `/set-model <provider:model-name>` to switch without restarting:
```
You: /set-model anthropic:claude-haiku-4-5
Switched → anthropic:claude-haiku-4-5
```

The new model is loaded first, behind a `switching model...` spinner, and the conversation carries
over. If it can't be loaded (wrong name, model not pulled, missing API key), Tzara keeps the current
model and says why.
Run `/list-models` to see the known models.

## Configuration

Defaults live in `TzaraConfig` ([src/tzara/config.py](src/tzara/config.py)) and can be
overridden with environment variables:

| Variable | Default | Meaning |
|----------|---------|---------|
| `TZARA_MODEL` | `ollama:qwen3:8b` | Model to start with, as `provider:model-name` |
| `TZARA_OLLAMA_NUM_CTX` | `16384` | Context window. Ollama's default of 4096 truncates the prompt |
| `TZARA_OLLAMA_KEEP_ALIVE` | `30m` | How long Ollama keeps the model loaded while idle |
| `TZARA_OLLAMA_REASONING` | `false` | Let thinking models such as Qwen3 think first; slower |
| `TZARA_OLLAMA_RESERVED_TOKENS` | `4096` | Part of the window kept for the prompt and reply; the rest is the history budget |
| `TZARA_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `TZARA_LOG_FILE` | platform default | Where to write the log |

```bash
TZARA_MODEL=ollama:llama3.1:8b tzara
```

A longer `TZARA_OLLAMA_KEEP_ALIVE` avoids the model-loading wait after long breaks, at the cost of
holding the model's memory (5-8 GB for an 8B model) while Tzara is idle.

### Memory

Tzara remembers the conversation, and any files it wrote, until you exit or type `/new`. History is
kept in memory only; nothing is saved to disk. It carries over when you switch models and survives
Ctrl+C. When a long conversation nears the model's context window, older turns are summarized
instead of being silently cut off.

### Logs

Tzara writes a rotating log file instead of printing to the chat:

| Platform | Default location |
|----------|------------------|
| macOS | `~/Library/Logs/tzara/tzara.log` |
| Linux | `~/.local/state/tzara/tzara.log` (or under `$XDG_STATE_HOME`) |
| Windows | `%LOCALAPPDATA%\tzara\Logs\tzara.log` |

When an error appears in the chat it says where the log is, and the full traceback is in the file.
At the default level the log has startup, model loads, switches and per-turn timing, but **not what
you or Tzara said**; message text is only logged with `TZARA_LOG_LEVEL=DEBUG`.

## Adding Custom Tools

Tools are small `BaseTool` classes with a typed input schema.

**Create a new tool** (`src/tzara/tools/weather.py`):
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

**Register the tool** in `Tzara.__init__` in `src/tzara/agent/agent.py`:
```python
from tzara.tools.calculator import Calculator
from tzara.tools.weather import Weather

self._tools = [Calculator(), Weather()]
```

That's it! Tzara automatically decides when to use tools based on context. Add a test in
`tests/` and run `make check`.


## Project Structure

```
tzara/
├── src/tzara/
│   ├── config.py         # All tunable settings (env overrides: TZARA_*)
│   ├── prompts.py        # System prompt and tool descriptions
│   ├── logs.py           # Log file setup
│   ├── agent/            # Deep Agent, conversation memory, harness setup, model factory
│   ├── cli/              # Chat loop and slash commands
│   └── tools/
│       ├── calculator.py # Safe math tool
│       └── ...           # Add custom tools here
├── tests/                # pytest suite (runs offline)
├── .github/workflows/    # CI
├── CLAUDE.md             # Developer guide
├── README.md             # This file
├── pyproject.toml        # Dependency ranges, linter and test configuration
└── requirements.lock     # Exact tested dependency versions
```

## Commands

Type these commands within the chat:

```
/help           Show all available commands
/list-models    Show all available models
/set-model      Switch to a different model
/current-model  Show active model
/new            Start a new conversation
/exit           Exit Tzara
```

## Learn More

- [LangChain DeepAgents](https://docs.langchain.com/oss/python/deepagents/overview) — Agent framework
- [LangGraph](https://python.langchain.com/docs/langgraph/) — Orchestration primitives
- [LangChain Tools](https://python.langchain.com/docs/modules/tools/) — Tool building
- [Model Context Protocol](https://modelcontextprotocol.io/) — Integration standard
- [Rich](https://rich.readthedocs.io/) — Terminal output styling

## Contributing

Contributions are welcome! Areas of interest:
- New tools (weather, APIs, databases, etc.)
- MCP server integrations
- Documentation and examples
- Bug fixes and improvements
- Feature suggestions

Run `make check` (ruff, pylint, mypy and the test suite) before opening a pull request; CI runs the
same checks. See [CLAUDE.md](CLAUDE.md) for the code conventions, dependency lockfile and git hooks.

Open an issue or discussion to get started.

## License

[MIT](/LICENSE)
