import logging
from typing import Annotated

import typer

from inspect_brief.core import export_results
from inspect_brief.parsing import parse_comma_separated, parse_target_metrics

app = typer.Typer(
    help="Inspect Brief: A CLI for generating concise metric summaries for Inspect evaluations."
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
) -> dict[str, list] | None:
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
            help="Path or list of paths to the Inspect evaluation log files (comma-separated)",
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
    configure_logging()
    export_results(
        log_dir=log_dir,
        log_files=parse_comma_separated(log_files),
        tasks=parse_comma_separated(tasks),
        target_metrics=parse_target_metrics_option(target_metrics),
        csv_path=csv_path,
        skip_existing=skip_existing,
    )


if __name__ == "__main__":
    app()
