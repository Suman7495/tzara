"""Entry point for ``python -m tzara``."""

from tzara.cli.launcher import Launcher

if __name__ == "__main__":
    raise SystemExit(Launcher.main())
