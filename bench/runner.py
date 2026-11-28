"""Runs benchmark tasks against a real Tzara server and scores them."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from bench.core import Outcome, Result, Step, Task


class BenchServer:
    """A Tzara server of its own: temporary data folder, free port, the fake Notion connected."""

    NOTION = Path(__file__).with_name("fake_notion.py")

    def __init__(self, model: str | None = None) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="tzarabench-"))
        self.notion_file = self.directory / "notion.json"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.model = model
        self._process: subprocess.Popen[bytes] | None = None
        self._client = httpx.Client(timeout=httpx.Timeout(300.0, connect=5.0))

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        """Start the server and wait until its model is loaded."""
        (self.directory / "mcp.json").write_text(
            json.dumps(
                {
                    "servers": {
                        "notion": {
                            "command": sys.executable,
                            "args": [str(self.NOTION)],
                            "env": {"BENCH_NOTION_FILE": str(self.notion_file)},
                            "tools": [
                                "notion-search",
                                "notion-fetch",
                                "notion-create-pages",
                                "notion-update-page",
                            ],
                        }
                    }
                }
            )
        )
        env = {
            **os.environ,
            "TZARA_DATA_DIR": str(self.directory),
            "TZARA_SERVER_PORT": str(self.port),
            "TZARA_LOG_FILE": str(self.directory / "tzara.log"),
        }
        if self.model:
            env["TZARA_MODEL"] = self.model
        self._process = subprocess.Popen(
            [sys.executable, "-m", "tzara", "serve"],
            env=env,
            stdout=(self.directory / "server.out").open("wb"),
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            time.sleep(1.5)
            try:
                if self._status().get("state") == "ready" and self._apps_ready():
                    return
            except (httpx.HTTPError, FileNotFoundError, KeyError):
                continue
        raise RuntimeError(f"the server did not become ready; see {self.directory}/tzara.log")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()

    # -- plumbing ----------------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + (self.directory / "token").read_text().strip()}

    def _status(self) -> dict[str, Any]:
        return self._client.get(self.base + "/v1/status", headers=self._headers()).json()

    def _apps_ready(self) -> bool:
        apps = self._client.get(self.base + "/v1/apps", headers=self._headers()).json()
        return not apps.get("reloading") and any(
            a["name"] == "notion" and a["connected"] for a in apps["apps"]
        )

    def reset(
        self, pages: dict[str, str], trashed: tuple[str, ...], facts: tuple[str, ...]
    ) -> None:
        """A clean slate for a task: only its facts remembered, no conversation, its pages."""
        self.notion_file.write_text(
            json.dumps(
                {
                    "pages": {
                        f"pg_{i}": {"title": t, "content": c}
                        for i, (t, c) in enumerate(pages.items())
                    },
                    "trashed": list(trashed),
                }
            )
        )
        for _ in range(20):
            if (
                self._client.delete(self.base + "/v1/memory", headers=self._headers()).status_code
                == 204
            ):
                break
            time.sleep(0.5)
        for fact in facts:
            self._chat("remember that " + fact, {})
        self._client.post(self.base + "/v1/conversation/new", headers=self._headers())

    def pages(self) -> dict[str, str]:
        data = json.loads(self.notion_file.read_text())
        return {str(p["title"]): str(p["content"]) for p in data["pages"].values()}

    # -- steps -------------------------------------------------------------------------------

    def run_step(self, step: Step) -> Outcome:
        started = time.monotonic()
        try:
            outcome = (
                self._write(step)
                if step.kind == "write"
                else self._chat(step.prompt, step.screen, step.place)
            )
        except Exception as error:
            outcome = Outcome(error=f"{error.__class__.__name__}: {error}")
        outcome.seconds = time.monotonic() - started
        return outcome

    def _chat(self, message: str, screen: dict[str, str], place: str = "") -> Outcome:
        body: dict[str, Any] = {"message": message}
        if screen:
            body["context"] = screen
        if place:
            body["place"] = place
        outcome = Outcome()
        event = ""
        with self._client.stream(
            "POST", self.base + "/v1/chat", json=body, headers=self._headers()
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.read().decode()[:200]}")
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[5:])
                    if event == "token":
                        outcome.text += data["text"]
                    elif event == "tool" and data.get("state") == "started":
                        outcome.tools.append(data["name"])
                    elif event == "error":
                        outcome.error = data.get("message", "error")
        return outcome

    def _write(self, step: Step) -> Outcome:
        body = {**step.screen, "field": step.field, "instruction": step.prompt}
        response = self._client.post(self.base + "/v1/write", json=body, headers=self._headers())
        if response.status_code != 200:
            return Outcome(error=f"HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        return Outcome(
            text=data["text"], changed=data.get("changed", True), mode=data.get("mode", "")
        )


class Runner:
    """Runs tasks one after another and keeps the results."""

    def __init__(self, server: BenchServer, log: Any = print) -> None:
        self.server = server
        self.log = log

    def run(self, tasks: list[Task], save: Any = None) -> list[Result]:
        results = []
        for number, task in enumerate(tasks, 1):
            result = self.run_task(task)
            results.append(result)
            if save is not None:
                save(results)  # a crash later does not lose what was measured
            mark = "PASS" if result.passed else "FAIL"
            why = (
                ""
                if result.passed
                else "  " + "; ".join(result.failures or [result.outcome.error])[:110]
            )
            self.log(
                f"[{number:3}/{len(tasks)}] {mark} {task.id:<28} "
                f"{result.outcome.seconds:5.1f}s{why}"
            )
        return results

    def run_task(self, task: Task) -> Result:
        self.server.reset(task.pages, task.trashed, task.facts)
        outcome = Outcome()
        failures: list[str] = []
        total = 0
        for index, step in enumerate(task.steps):
            outcome = self.server.run_step(step)
            if index == len(task.steps) - 1:
                try:
                    outcome.pages = self.server.pages()
                except Exception as error:
                    outcome.error = outcome.error or f"could not read the pages: {error}"
            checks = step.checks + (task.checks if index == len(task.steps) - 1 else ())
            for check in checks:
                total += 1
                if outcome.error:
                    break
                reason = check.test(outcome)
                if reason:
                    failures.append(f"{check.__class__.__name__}: {reason}")
            if outcome.error:
                break
        return Result(task, outcome, failures, total)


class Report:
    """Tables of how it went, by area and by category."""

    @staticmethod
    def table(results: list[Result], key: str) -> list[str]:
        groups: dict[str, list[Result]] = defaultdict(list)
        for result in results:
            groups[getattr(result.task, key)].append(result)
        lines = [f"{key:<12} {'tasks':>5} {'passed':>7} {'score':>7} {'avg s':>7}"]
        for name, group in sorted(groups.items()):
            lines.append(
                f"{name:<12} {len(group):>5} {sum(r.passed for r in group):>4}/{len(group):<2} "
                f"{100 * sum(r.score for r in group) / len(group):>6.0f}% "
                f"{sum(r.outcome.seconds for r in group) / len(group):>7.1f}"
            )
        return lines

    @classmethod
    def text(cls, results: list[Result], model: str) -> str:
        passed = sum(r.passed for r in results)
        score = 100 * sum(r.score for r in results) / max(len(results), 1)
        lines = [
            "",
            f"TzaraBench  model {model}  {len(results)} tasks",
            f"PASSED {passed}/{len(results)}   mean score {score:.0f}%",
            "",
            *cls.table(results, "area"),
            "",
            *cls.table(results, "category"),
        ]
        failed = [r for r in results if not r.passed]
        if failed:
            lines += ["", "Failed:"]
            lines += [
                f"  {r.task.id}: " + "; ".join(r.failures or [r.outcome.error])[:150]
                for r in failed
            ]
        return "\n".join(lines)
