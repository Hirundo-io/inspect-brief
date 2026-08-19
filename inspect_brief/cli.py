import json
from pathlib import Path
from typing import Annotated, get_type_hints

import typer

from inspect_brief.core import InspectScore, export_results

_INSPECT_SCORE_KEYS = set(get_type_hints(InspectScore))

app = typer.Typer(
    help="Inspect Brief: A CLI for generating concise metric summaries for Inspect evaluations."
)


def parse_comma_separated(value: str | None) -> list[str] | None:
    """Parse a comma-separated CLI string into a list of non-empty stripped items."""
    if value is None:
        return None
    items = [item.strip() for item in value.split(",")]

    return [item for item in items if item] or None


def parse_target_metrics(
    value: str | None,
) -> dict[str, list[InspectScore]] | None:
    """Parse --target-metrics JSON (inline string or path to a JSON file)."""
    if value is None:
        return None

    path = Path(value)
    raw = path.read_text(encoding="utf-8") if path.is_file() else value
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise typer.BadParameter(
            "Must be a JSON object (or path to one) mapping "
            "task name -> list of InspectScore objects with keys "
            f"{sorted(_INSPECT_SCORE_KEYS)}"
        ) from e

    if not isinstance(parsed, dict):
        raise typer.BadParameter("Must be a JSON object mapping task -> list of metrics")

    result: dict[str, list[InspectScore]] = {}
    for task, metrics in parsed.items():
        if not isinstance(task, str) or not isinstance(metrics, list):
            raise typer.BadParameter(
                f"Task '{task}' must map to a list of InspectScore objects"
            )
        scores: list[InspectScore] = []
        for i, metric in enumerate(metrics):
            if not isinstance(metric, dict) or set(metric) != _INSPECT_SCORE_KEYS:
                raise typer.BadParameter(
                    f"Metric {i} for task '{task}' must be an object with exactly "
                    f"keys {sorted(_INSPECT_SCORE_KEYS)}"
                )
            scores.append(
                InspectScore(
                    name=metric["name"],
                    is_percentage=bool(metric["is_percentage"]),
                    is_higher_better=bool(metric["is_higher_better"]),
                    is_normalized=bool(metric["is_normalized"]),
                )
            )
        result[task] = scores

    return result


@app.command(
    context_settings={"allow_extra_args": False, "ignore_unknown_options": False}
)
def main(
    log_dir: Annotated[
        str | None,
        typer.Option(
            "--log-dir",
            help="Directory containing the Inspect evaluation logs",
        ),
    ] = None,
    log_files: Annotated[
        str | None,
        typer.Option(
            "--log-files",
            help="Path or list of paths to the Inspect evaluation log file/s (comma-separated)",
        ),
    ] = None,
    tasks: Annotated[
        str | None,
        typer.Option(
            "--tasks",
            help="The tasks to include in the results (comma-separated)",
        ),
    ] = None,
    target_metrics: Annotated[
        str | None,
        typer.Option(
            "--target-metrics",
            help=(
                "JSON object (or path to a JSON file) mapping task -> list of "
                'InspectScore objects: {"task":[{"name":"accuracy",'
                '"is_percentage":true,"is_higher_better":true,"is_normalized":true}]}'
            ),
        ),
    ] = None,
    csv_path: Annotated[
        str | None,
        typer.Option(
            "--csv-path",
            help=(
                "The path to the output CSV file. "
                "If not provided, the output will be saved to 'brief_results.csv' "
                "in the current working directory or the log_dir if provided."
            ),
        ),
    ] = None,
    skip_existing: Annotated[
        bool,
        typer.Option(
            "--skip-existing",
            help="Whether to skip tasks with existing results",
        ),
    ] = False,
) -> None:
    """
    Prepare the results of the evaluation for CSV export.

    CLI format:
        inspect-brief --log-dir <log_dir> --log-files <log_files> --tasks <tasks> --target-metrics <target_metrics> --csv-path <csv_path> --skip-existing
    """
    # Generate the concise metric summaries for the Inspect evaluations
    # and export them to a CSV file
    export_results(
        log_dir=log_dir,
        log_files=parse_comma_separated(log_files),
        tasks=parse_comma_separated(tasks),
        target_metrics=parse_target_metrics(target_metrics),
        csv_path=csv_path,
        skip_existing=skip_existing,
    )


if __name__ == "__main__":
    app()
