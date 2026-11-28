"""``python -m bench``: run TzaraBench against a fresh Tzara server and print the scores."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from bench.runner import BenchServer, Report, Runner
from bench.tasks import TaskSet


class Cli:
    """The command line."""

    @classmethod
    def main(cls) -> None:
        parser = argparse.ArgumentParser(prog="python -m bench", description=__doc__)
        parser.add_argument(
            "--model", default="ollama:qwen3:8b", help="provider:model (default %(default)s)"
        )
        parser.add_argument(
            "--area", action="append", help="only this area (email, teams, linkedin, notion, ...)"
        )
        parser.add_argument("--category", action="append", help="only this category")
        parser.add_argument("--only", help="only these task ids, comma separated")
        parser.add_argument("--limit", type=int, help="run at most this many tasks")
        parser.add_argument(
            "--offline", action="store_true", help="skip tasks that need the internet"
        )
        parser.add_argument("--list", action="store_true", help="list the tasks and exit")
        parser.add_argument(
            "--out", help="where to save the results (default bench/results/<time>.json)"
        )
        args = parser.parse_args()

        tasks = TaskSet.all()
        if args.area:
            tasks = [t for t in tasks if t.area in args.area]
        if args.category:
            tasks = [t for t in tasks if t.category in args.category]
        if args.only:
            wanted = set(args.only.split(","))
            tasks = [t for t in tasks if t.id in wanted]
        if args.offline:
            tasks = [t for t in tasks if not t.network]
        if args.limit:
            tasks = tasks[: args.limit]
        if args.list:
            for task in tasks:
                print(f"{task.id:<30} {task.area:<9} {task.category}")
            print(f"{len(tasks)} tasks")
            return

        out = (
            Path(args.out)
            if args.out
            else Path(__file__).parent / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)

        def save(results: list) -> None:
            out.write_text(json.dumps([r.to_json() for r in results], indent=1))

        server = BenchServer(args.model)
        print(f"starting a server on port {server.port} with {args.model} ...", flush=True)
        server.start()
        try:
            results = Runner(server, lambda line: print(line, flush=True)).run(tasks, save)
        finally:
            server.stop()
        print(Report.text(results, args.model))
        print(f"\nresults saved to {out}")


if __name__ == "__main__":
    Cli.main()
