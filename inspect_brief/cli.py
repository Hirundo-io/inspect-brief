"""Command-line interface for exporting Inspect evaluation summaries."""

import logging
from pathlib import Path
from typing import Annotated

import typer

from inspect_brief.core import InspectScore, export_results
from inspect_brief.parsing import parse_comma_separated, parse_target_metrics

app = typer.Typer(
    help=(
        "Inspect Brief: A CLI for generating concise metric summaries for "
        "Inspect evaluations."
    ),
)


def configure_logging() -> None:
    """Configure user-facing logging for the standalone CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_target_metrics_option(
    value: str | None,
) -> dict[str, list[InspectScore]] | None:
    """Convert shared target-metrics validation errors into CLI parameter errors.

    Args:
        value: Inline target-metrics JSON or a path to a JSON file.

    Returns:
        Target metrics grouped by task, or None when no value is supplied.

    Raises:
        typer.BadParameter: If the supplied target metrics are invalid.
    """
    try:
        return parse_target_metrics(value, source="--target-metrics")
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@app.command(
    context_settings={"allow_extra_args": False, "ignore_unknown_options": False},
    help="Prepare the results of the evaluation for CSV export.",
)
def main(
    log_dir: Annotated[
        Path | None,
        typer.Option(
            "--log-dir",
            help="Directory containing the Inspect evaluation logs",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
    # This remains a string because the option accepts a comma-separated collection;
    # a Typer Path would validate the complete value as one file path.
    log_files: Annotated[
        str | None,
        typer.Option(
            "--log-files",
            metavar="PATH[,PATH...]",
            help=(
                "Path or comma-separated list of paths to the Inspect evaluation "
                "log files"
            ),
        ),
    ] = None,
    tasks: Annotated[
        str | None,
        typer.Option(
            "--tasks",
            help="The tasks to include in the results (comma-separated)",
        ),
    ] = None,
    # This remains a string because the value can be inline JSON or a file path;
    # Typer does not support a str | Path union for one option.
    target_metrics: Annotated[
        str | None,
        typer.Option(
            "--target-metrics",
            metavar="JSON|PATH",
            help=(
                "JSON object (or path to a JSON file) mapping task -> list of "
                'InspectScore objects: {"task":[{"name":"accuracy",'
                '"is_percentage":true,"is_higher_better":true,"is_normalized":true}]}'
            ),
        ),
    ] = None,
    csv_path: Annotated[
        Path | None,
        typer.Option(
            "--csv-path",
            help=(
                "The path to the output CSV file. "
                "If not provided, the output will be saved to 'brief_results.csv' "
                "in the current working directory or the log_dir if provided."
            ),
            exists=False,
            file_okay=True,
            dir_okay=False,
            writable=True,
            readable=True,
            resolve_path=True,
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
    """Prepare the results of the evaluation for CSV export.

    Raises:
        typer.Exit: If an operational error prevents the export.
    """
    configure_logging()
    try:
        export_results(
            log_dir=str(log_dir) if log_dir else None,
            log_files=parse_comma_separated(log_files),
            tasks=parse_comma_separated(tasks),
            target_metrics=parse_target_metrics_option(target_metrics),
            csv_path=str(csv_path) if csv_path else None,
            skip_existing=skip_existing,
            fail_on_log_error=True,
        )
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error


if __name__ == "__main__":
    app()
