# TzaraBench

Does Tzara do the work of a chief of staff? The unit tests (`make check`) guard behaviour; this measures
how well the agent does real tasks. It starts its own Tzara server (temporary data folder, free port,
a fake Notion connected as an MCP app), sends each task with what the agent would have on a Mac (the
screen text, the field, the apps), and checks the outcome.

```bash
python -m bench                      # all tasks, local model (ollama:qwen3:8b)
python -m bench --area email         # email, teams, linkedin, notion, context, memory, web, recovery
python -m bench --category writing   # context, tools, multistep, research, memory, writing, recovery
python -m bench --only email-ask,teams-blocked --offline
python -m bench --model anthropic:claude-sonnet-5-5   # compare models (needs the key)
python -m bench --list
```

Results print as tables by area and by category and are saved to `bench/results/*.json` (ignored by git):
compare two runs to see whether a change helped. A task passes only if every check on it holds; the score
is the share of checks that hold.

- `core.py` the checks (`Has`, `Lacks`, `Language`, `Used`, `PageHas`, ...), `Step` and `Task`.
- `fixtures.py` the email thread, the Teams chat, the LinkedIn profile and the Notion pages the tasks use.
- `tasks.py` the tasks. Add one by adding a line: a prompt, a screen, and what must be true.
- `fake_notion.py` an MCP server with Notion's tool names (`notion-search`, `notion-fetch`,
  `notion-create-pages`, `notion-update-page`) over a JSON file, so a task can check what was done to a page.
- `runner.py` the server, the runner and the report.

Checks are programmatic (a fact is present, a made-up one is not, the language, the length, the tool that ran,
the page afterwards). Writing *quality* is only measured by proxies for now (length, register, no invented
signature); judging it needs a stronger model or a person.
