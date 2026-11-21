.PHONY: install install-latest lock upgrade format lint typecheck test check

# Install the exact versions in requirements.lock (what CI and the tests use).
install:
	pip install -r requirements.lock
	pip install --no-deps -e .

# Install the newest versions pyproject.toml allows, ignoring the lock.
install-latest:
	pip install -e ".[dev]"

# Re-resolve requirements.lock, keeping the versions it already pins.
lock:
	uv pip compile pyproject.toml --extra dev --universal --python-version 3.11 \
		--output-file requirements.lock

# Re-resolve requirements.lock to the newest allowed versions, then run `make check`.
upgrade:
	uv pip compile pyproject.toml --extra dev --universal --python-version 3.11 \
		--upgrade --output-file requirements.lock

format:
	ruff format src tests
	ruff check --fix src tests

lint:
	ruff format --check src tests
	ruff check src tests
	pylint src/tzara

typecheck:
	mypy

test:
	pytest --cov --cov-report=term-missing

check: lint typecheck test
