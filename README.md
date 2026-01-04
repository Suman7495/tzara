<p align="center">
  <img src="assets/banner.png" alt="Tzara banner" width="100%">
</p>

# Tzara: A Personal AI Agent
Tzara is a personal AI assistant that helps simplify day-to-day tasks.

It is designed for practical use: reducing mental overhead, clarifying tasks, and helping you stay oriented without turning productivity into a system you have to maintain.

## Available Tools for Tzara

- [x] Calculator: evaluates mathematical expressions


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

## Run Tzara

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

## Contributing

Contributions are welcome, especially:
- Feedback from real usage
- Suggestions for practical workflows
- Small, focused improvements

Open an issue or discussion to get started.

## License
[MIT](/LICENSE)



