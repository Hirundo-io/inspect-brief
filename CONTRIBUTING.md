# Contributing to inspect-brief

Thanks for contributing. This guide covers local setup and the checks a change
must pass before review.

Working with an AI coding agent? [`AGENTS.md`](AGENTS.md) holds the repository
guidelines agents should follow, including project structure, coding style, and
testing conventions.

## Development installation

The commands below install the project from a local repository checkout. Run
them from the repository root:

```bash
uv venv .venv
source .venv/bin/activate
uv sync
```

Install the documentation dependencies when working on the Sphinx sources:

```bash
uv sync --group docs
```

Developers working from the repository root can also invoke the package
directly:

```bash
python -m inspect_brief [OPTIONS]
```

## Verification

Run the same checks CI enforces before opening a pull request:

```bash
uv run ruff format --check --diff
uv run ruff check .
uv run pyrefly check
uv run pytest -q
uv run --group docs sphinx-build -W -b html docs docs/_build/html
```

During development, run the tests implicated by your change first and the full
set once the change is ready.

## Pull requests

Use imperative, concise commit titles. Each pull request should explain
behavior changes, identify CSV schema or CLI compatibility effects, and link the
tracking issue. Include representative CLI output or CSV snippets for
user-visible changes.
