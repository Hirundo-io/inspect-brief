# Repository Guidelines

## Project Structure & Module Organization

Core package code lives in `inspect_brief/`:

- `cli.py` — Typer CLI (`inspect-brief` entry point)
- `core.py` — log loading, metric extraction, CSV export
- `__main__.py` — `python -m inspect_brief` entry point

Tests should reside in `tests/` and mirror module names when added. Generated summary CSVs and evaluation artifacts should stay under ignored output directories; distribution artifacts in `dist/` should not be edited manually.

## Build, Test, and Development Commands

- `uv venv && source .venv/bin/activate`: create and activate the dedicated virtual environment before running tooling. Install dependencies into `.venv` rather than the system interpreter.
- `uv sync`: install the project locally (and `uv sync --group dev` for development tools).
- Prefer `uv` for dependency management in automation scripts and local workflows; avoid invoking `pip` directly unless you are installing `uv` itself or a tool explicitly requires `pip`.
- Run the CLI through the package entry point (`inspect-brief ...`) or module form (`python -m inspect_brief ...`) rather than executing files by path.
- This project uses `pytest`.
- Run `ruff format .` (or `ruff format --check --diff` to verify) before committing to ensure consistent styling, followed by `ruff check .` for linting.
- `pyrefly check`: run static type checks (CI uses the same configuration).

## Coding Style & Naming Conventions

Follow PEP 8 defaults with 4-space indentation. Prefer `snake_case` for modules, functions, and variables; reserve `PascalCase` for classes and `UPPER_SNAKE_CASE` for constants. Keep public CLI options descriptive and aligned with existing Typer option names. Let `ruff` fix spacing and import order; avoid disabling rules unless there is a clear justification. Type hints are expected on new public functions. Keep CSV schema (`OutputEntry`) and `InspectScore` definitions in `core.py`; keep argument parsing and validation in `cli.py`.

## Testing Guidelines

Add or update `tests/test_*.py` files alongside any new feature. Use `pytest` assertions, fixtures, and `monkeypatch` for mocking filesystem I/O and Inspect log reads (avoid `unittest.mock`). Cover CLI parsing in `cli.py` (comma-separated lists, `--target-metrics` JSON/file), module execution via `__main__.py`, log loading/filtering, metric formatting, and CSV export/append behavior when those areas change. Keep simulated Inspect logs deterministic so runs remain reproducible.

## Commit & Pull Request Guidelines

Write imperative, concise commit titles (e.g., `Add Created column to CSV export`). Squash trivial fixups locally before raising a PR. Each PR should explain behavior changes, note impacts on CSV schema or CLI flags, and link to any tracking issue. Attach sample CLI output or CSV snippets when the change affects user-visible results.

## Security & Configuration Tips

No secrets should live in the repo. Use environment variables for any provider tokens. Prefer loading local secrets from an untracked `.env` via [`python-dotenv`](https://pypi.org/project/python-dotenv/) when needed, and never commit `.env` files or their contents. Verify that large evaluation logs and generated CSVs stay out of version control.
