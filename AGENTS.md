# Repository Guidelines

## Project Structure

Core package code lives in `inspect_brief/`:

- `cli.py` — Typer option declarations, path validation, and CLI-specific error translation
- `core.py` — log loading, metric extraction, and CSV export
- `parsing.py` — parsing and validation shared by the CLI and hook
- `hooks.py` — Inspect task and run lifecycle integration
- `_registry.py` — Inspect extension registration
- `__main__.py` — `python -m inspect_brief` entry point

Tests live in `tests/` and mirror the package modules. Sphinx sources live in `docs/`. Generated summaries, evaluation logs, documentation builds, and distribution artifacts must remain in ignored output directories.

## Environment and Dependencies

- Create and activate the dedicated environment with `uv venv && source .venv/bin/activate`.
- Install development dependencies with `uv sync` and documentation dependencies with `uv sync --group docs`.
- Prefer `uv` for dependency management and automation. Do not use `pip` unless installing `uv` itself or a tool explicitly requires it.
- Run the CLI through `inspect-brief` or `python -m inspect_brief`, not by executing package files directly.

## Verification

Run the same checks enforced by CI before committing:

```bash
uv run ruff format --check --diff
uv run ruff check .
uv run pyrefly check
uv run pytest -q
uv run --group docs sphinx-build -W -b html docs docs/_build/html
```

CI runs the test suite on every supported Python version. During development, run focused tests first and the complete verification set when the change is ready.

## Coding Style

Follow Google-style docstrings and PEP 8 naming conventions. Use modern strict type annotations on all functions, including tests. Keep shared parsing and validation in `parsing.py`; keep Typer-specific option declarations and error translation in `cli.py`. Keep the CSV schema (`OutputEntry`) and score definition (`InspectScore`) in `core.py`.

Let Ruff handle formatting and import order. Avoid disabling lint rules without a documented reason.

## Testing

Use pytest assertions, fixtures, and `monkeypatch`; do not use `unittest.mock`. Test observable behavior through the narrowest public API that covers it. Prefer real objects over mocks, keep tests deterministic and isolated, and avoid duplicate coverage.

## Pull Requests

Use imperative, concise commit titles. Each PR should explain behavior changes, identify CSV schema or CLI compatibility effects, and link the tracking issue. Include representative CLI output or CSV snippets for user-visible changes.

## Security and Hook Configuration

Do not commit secrets, evaluation logs, or generated CSVs. The Inspect hook loads its local configuration from an untracked `.env` file at import time through `python-dotenv`; keep this guidance scoped to hook configuration rather than general provider credential management.
