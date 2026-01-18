<p align="center">
  <img src="assets/tzara_banner.png" alt="Tzara banner" width="100%">
</p>

# Tzara: Your Cute Personal AI Agent
Tzara is an open-source, **local-first personal AI agent** designed for developers who want to learn how to create AI agents in Python. 

It can chat, do some math, and soon will do some more cool stuff.

Tzara runs fully locally. No cloud. Complete data privacy.


## Current Features

- [x] Calculator — evaluates mathematical expressions via a tool
- [x] Streaming CLI interface for conversations
- [x] Local LLM execution, no cloud hosting, complete data privacy


## Architecture Overview

Tzara is composed of three simple layers:

1. **LLM layer**:  
   - Backed by Ollama (default: `llama3.1:8b`)
   - Tool calling enabled

2. **Agent orchestration**:  
   - Built with LangGraph
   - Explicit control flow: LLM → tools → LLM
   - No hidden chains or implicit behavior

3. **Tools** : 
   - Plain Python functions
   - Registered explicitly
   - Easy to add, remove, or modify

The goal is to keep the core loop obvious and make extension frictionless.

## Dependencies
Download [Ollama](https://ollama.com/download) and install Ollama onto the available supported platforms (including Windows Subsystem for Linux aka WSL, macOS, and Linux)

Fetch available LLM model via `ollama pull <name-of-model>`. 

Particularly, for Tzara, we use `llama3.1:8b`:
``` bash
ollama pull llama3.1:8b
```

You can check that the model was correctly installed with:
```bash
ollama list
```

## Installation
Clone the repo:
``` python
git clone https://github.com/Suman7495/tzara.git
```

Change directory into the repo:
``` bash
cd tzara
```

Install Tzara:

``` bash
pip install -e .
```

## Quickstart

Launch Tzara from your terminal:
``` bash
tzara
```

Your expected results:
```bash
Tzara is online. Type 'exit' to quit.

You: 
```

To exit, simply type: `exit`

## Adding a Tool (2 minutes example)

Tools in Tzara are just Python functions.

Create a tool:
```python
# src/tzara/tools/hello.py
from langchain.tools import tool

@tool
def hello(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"
```

Register the tool in `src/tzara/agent.py`:

``` python
# src/tzara/agent.py
from tzara.tools import hello

class Tzara:
    ... 

    self._tools = [
        ...,
        hello.hello,
    ]
```

That’s it.

Tzara will now automatically decide when to use the tool based on user input.

## Who Is This For?
- Developers experimenting with AI agents in Python
- People who want a local, hackable assistant with complete data privacy.

Tzara is not:

- A polished consumer assistant
- A cloud-hosted product
- A no-code or agentic automation platform

## Contributing

Contributions are welcome, especially:
- Feedback from real usage
- Suggestions for practical workflows
- Small, focused improvements

Open an issue or discussion to get started.

## License
[MIT](/LICENSE)



