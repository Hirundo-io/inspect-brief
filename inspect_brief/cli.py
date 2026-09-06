"""Command-line interface for exporting Inspect evaluation summaries."""

import logging
from pathlib import Path
from typing import Annotated

import typer
from tqdm import tqdm

from inspect_brief.core import InspectScore, export_results
from inspect_brief.parsing import (
    parse_comma_separated,
    parse_log_files,
    parse_target_metrics,
)

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


def sanitize_terminal_text(value: str) -> str:
    """Escape characters that can control or rewrite terminal output.

    Args:
        value: Untrusted text intended for terminal display.

    Returns:
        Text with non-printable characters replaced by visible escape sequences.
    """
    return "".join(
        character if character.isprintable() else repr(character)[1:-1]
        for character in value
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


def parse_log_files_option(values: list[str] | None) -> list[str] | None:
    """Convert shared log-file validation errors into CLI parameter errors.

    Args:
        values: Raw option values, each of which may contain comma-separated
            paths or filesystem URIs.

    Returns:
        Resolved local paths and unchanged filesystem URIs, or None when no
        sources are supplied.

    Raises:
        typer.BadParameter: If a path does not identify a readable file.
    """
    try:
        return parse_log_files(values)
    except ValueError as error:
        raise typer.BadParameter(
            str(error),
            param_hint="--log-files",
        ) from error


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
    log_files: Annotated[
        list[str] | None,
        typer.Option(
            "--log-files",
            metavar="SOURCE[,SOURCE...]",
            help=(
                "Paths or filesystem URIs for Inspect evaluation logs; repeat "
                "the option or separate sources with commas"
            ),
        ),
    ] = None,
    tasks: Annotated[
        list[str] | None,
        typer.Option(
            "--tasks",
            metavar="TASK[,TASK...]",
            help=(
                "The tasks to include in the results; repeat the option or "
                "separate task names with commas"
            ),
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
        parsed_log_files = parse_log_files_option(log_files)
        parsed_tasks = parse_comma_separated(tasks)
        parsed_target_metrics = parse_target_metrics_option(target_metrics)
        with tqdm(
            desc="Preparing Inspect tasks",
            unit="task",
            dynamic_ncols=True,
            disable=None,
        ) as progress:

            def update_progress(
                completed_log_count: int,
                total_log_count: int,
                current_task_name: str,
            ) -> None:
                """Update the standalone CLI progress display.

                Args:
                    completed_log_count: Number of logs processed so far.
                    total_log_count: Total number of logs to process.
                    current_task_name: Name of the task being processed.

                """
                progress.total = total_log_count
                progress.set_postfix_str(
                    sanitize_terminal_text(current_task_name),
                    refresh=False,
                )
                progress.update(completed_log_count - progress.n)

            export_results(
                log_dir=str(log_dir) if log_dir else None,
                log_files=parsed_log_files,
                tasks=parsed_tasks,
                target_metrics=parsed_target_metrics,
                csv_path=str(csv_path) if csv_path else None,
                skip_existing=skip_existing,
                fail_on_log_error=True,
                log_progress=False,
                progress_callback=update_progress,
            )
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error


if __name__ == "__main__":
    app()
