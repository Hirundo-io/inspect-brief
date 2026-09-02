"""Load Inspect logs and export their metric summaries to CSV."""

import csv
import logging
import os
import stat
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TypedDict, get_type_hints
from urllib.parse import urlsplit, urlunsplit

from inspect_ai.log import EvalLog, EvalScore, read_eval_log, write_eval_log

logger = logging.getLogger(__name__)

type ProgressCallback = Callable[[int, int, str], None]


class InspectScore(TypedDict):
    """Score definition for inspect-ai.

    Args:
        name: The name of the score metric in the inspect-ai logs.
        is_percentage: Whether the score is a percentage.
        is_higher_better: Whether a higher value is better.
        is_normalized: Whether the score is normalized. Relevant only for
            percentage scores.

    """

    name: str
    is_percentage: bool
    is_higher_better: bool
    is_normalized: bool


OutputEntry = TypedDict(
    "OutputEntry",
    {
        "Created": str,
        "Run ID": str,
        "Benchmark": str,
        "Metric": str,
        "Score": float | str,
        "Runtime (sec)": int | str,
    },
)


def _discover_log_paths(
    log_dir: str, load_errors: list[tuple[str, Exception]]
) -> list[str]:
    """Recursively discover evaluation-log paths while recording scan failures.

    Args:
        log_dir: Directory to scan recursively for evaluation logs.
        load_errors: Mutable collection that receives directory scan failures.

    Returns:
        Discovered `.eval` file paths, or an empty list when scanning fails.

    """
    try:
        return [str(path) for path in Path(log_dir).rglob("*.eval")]
    except OSError as error:
        logger.warning("❌ Could not scan Inspect log directory %s: %s", log_dir, error)
        load_errors.append((f"{log_dir} (directory scan)", error))
        return []


def _resolve_eval_log_path(log_file: str) -> Path:
    """Validate and resolve a local Inspect evaluation-log path.

    Args:
        log_file: Path supplied to the log loader.

    Returns:
        The canonical path to the `.eval` log.

    Raises:
        ValueError: If the supplied path is not an `.eval` file.
    """
    path = Path(log_file)
    if path.suffix != ".eval":
        raise ValueError("Inspect Brief currently supports only .eval log files")
    return path.resolve(strict=True)


def _is_log_uri(log_file: str) -> bool:
    """Return whether a log source uses an explicit filesystem URI scheme.

    Args:
        log_file: Log source supplied to the loader.

    Returns:
        True for sources such as ``s3://bucket/run.eval`` and ``file:///run.eval``.
    """
    return "://" in log_file and bool(urlsplit(log_file).scheme)


def _eval_log_source_key(log_file: str) -> str | Path:
    """Validate a log source and return its stable deduplication key.

    Args:
        log_file: Local path or filesystem URI supplied to the loader.

    Returns:
        The unchanged URI for remote/provider sources, or the canonical local path.

    Raises:
        ValueError: If the supplied source is not an `.eval` log.
    """
    if not _is_log_uri(log_file):
        return _resolve_eval_log_path(log_file)

    if PurePosixPath(urlsplit(log_file).path).suffix != ".eval":
        raise ValueError("Inspect Brief currently supports only .eval log files")
    return log_file


def _json_sidecar_path(log_file: str) -> str | Path:
    """Return the JSON sidecar location for a local path or filesystem URI.

    Args:
        log_file: Local path or filesystem URI for an Inspect evaluation log.

    Returns:
        A sibling location with a `.json` suffix.
    """
    if not _is_log_uri(log_file):
        return Path(log_file).with_suffix(".json")

    parsed = urlsplit(log_file)
    return urlunsplit(
        parsed._replace(path=str(PurePosixPath(parsed.path).with_suffix(".json"))),
    )


def get_runtime_from_timestamps(started_at: str, completed_at: str) -> int | str:
    """Calculate runtime (in seconds) from ISO format timestamp strings.

    Args:
        started_at: The start timestamp.
        completed_at: The end timestamp.

    Returns:
        The runtime in seconds.
        "N/A" if the runtime cannot be calculated.

    """
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(completed_at)
        duration = end - start
        # Strip microseconds for cleaner display
        duration_seconds = int(duration.total_seconds())
        return duration_seconds
    except (OverflowError, ValueError):
        return "N/A"


def load_logs(
    log_dir: str | None = None,
    log_files: str | list[str] | None = None,
    export_jsons: bool = False,
    fail_on_error: bool = False,
) -> list[EvalLog]:
    """Load the Inspect evaluation logs from the directory or files.

    Args:
        log_dir (optional): Path to the directory containing the Inspect logs.
        log_files (optional): Path or list of paths to the Inspect evaluation
            log files.
        export_jsons (optional): Whether to export the Inspect logs as JSON files.
        fail_on_error: Whether to raise after processing all paths if any log
            fails to load.

    Returns:
        The list of Inspect evaluation logs.

    Raises:
        ValueError: If no input is provided, or if ``fail_on_error`` is true
            and one or more logs cannot be loaded.

    """
    if not log_dir and not log_files:
        raise ValueError("At least one of log_dir or log_files must be provided")
    if isinstance(log_files, str):
        log_files = [log_files]
    paths = list(log_files or [])
    load_errors: list[tuple[str, Exception]] = []
    if log_dir:
        paths.extend(_discover_log_paths(log_dir, load_errors))
    logs: list[EvalLog] = []
    source_keys: set[str | Path] = set()
    for log_file in paths:
        try:
            # Canonicalize local paths for deduplication while preserving provider
            # URIs so Inspect's fsspec-backed reader can handle them unchanged.
            source_key = _eval_log_source_key(log_file)
            if source_key in source_keys:
                continue
            source_keys.add(source_key)
            log = read_eval_log(log_file)
            logs.append(log)
            if export_jsons:
                logger.info("📝 Exporting Inspect log %s as JSON", log_file)
                try:
                    write_eval_log(log, _json_sidecar_path(log_file), format="json")
                except Exception:  # ruff: ignore[blind-except] -- Inspect writers expose no common error type.
                    logger.warning("❌ Could not export Inspect log %s", log_file)
        # Inspect log readers and storage backends may raise provider-specific
        # exceptions. Isolate each path so one malformed log does not stop the rest.
        except Exception as error:  # ruff: ignore[blind-except]
            logger.warning("❌ Could not load Inspect log %s: %s", log_file, error)
            load_errors.append((log_file, error))

    if load_errors and fail_on_error:
        failed_paths = ", ".join(path for path, _ in load_errors)
        raise ValueError(
            f"Could not load {len(load_errors)} Inspect log(s): {failed_paths}"
        ) from load_errors[0][1]

    return logs


def target_metric_name(target_metric: InspectScore) -> str:
    """Format a configured metric name for CSV output.

    Args:
        target_metric: The configured metric definition.

    Returns:
        The metric name annotated with percentage and direction metadata.

    """
    metric_name = target_metric["name"]
    if target_metric["is_percentage"]:
        metric_name += " (%)"

    return metric_name + (" ⬆️" if target_metric["is_higher_better"] else " ⬇️")


def failed_score_values(
    status: str, target_metrics: list[InspectScore] | None
) -> dict[str, float | str]:
    """Create CSV score values for an evaluation that did not succeed.

    Args:
        target_metrics (optional): Configured metrics to include in the output.
        status: The Inspect evaluation status.
        target_metrics: Optional configured metrics to include in the output.

    Returns:
        A status value or one failure value for each configured metric.

    """
    if not target_metrics:
        return {"status": f"Failed ({status})"}

    return {
        target_metric_name(target_metric): f"Failed ({status})"
        for target_metric in target_metrics
    }


def all_score_values(scores: list[EvalScore]) -> dict[str, float | str]:
    """Extract every metric value from Inspect scorer results.

    Args:
        scores: The scorer results recorded in an Inspect evaluation log.

    Returns:
        Metric names mapped to their recorded values. Names include scorer
        prefixes when more than one scorer produced metrics.

    """
    add_scorer_prefix = len(scores) > 1
    score_values: dict[str, float | str] = {}
    for score in scores:
        for metric_name, metric in score.metrics.items():
            if add_scorer_prefix:
                metric_name = f"{score.name}: {metric_name}"
            score_values[metric_name] = metric.value

    return score_values


def target_score_values(
    scores: list[EvalScore], target_metrics: list[InspectScore]
) -> dict[str, float | str]:
    """Extract configured metric values from Inspect scorer results.

    Args:
        scores: The scorer results recorded in an Inspect evaluation log.
        target_metrics: The metric definitions to include in the output.

    Returns:
        Configured metric names mapped to their values or missing-metric
        messages.

    """
    score_values: dict[str, float | str] = {}
    for target_metric in target_metrics:
        metric_name = target_metric_name(target_metric)
        for score in scores:
            if target_metric["name"] not in score.metrics:
                continue
            metric_value = score.metrics[target_metric["name"]].value
            if target_metric["is_percentage"] and target_metric["is_normalized"]:
                metric_value *= 100.0
            score_values[metric_name] = metric_value
            break
        else:
            score_values[metric_name] = f"Metric '{target_metric['name']}' not found"

    return score_values


def score_values(
    log: EvalLog, target_metrics: list[InspectScore] | None
) -> dict[str, float | str]:
    """Select score values for an Inspect evaluation log.

    Args:
        log: The Inspect evaluation log to summarize.
        target_metrics: Optional configured metrics to include.

    Returns:
        The selected scores, a failure status, or a no-scores status.

    """
    if target_metrics == []:
        return {}
    if log.status != "success":
        return failed_score_values(log.status, target_metrics)
    if not log.results or not log.results.scores:
        if target_metrics:
            return target_score_values([], target_metrics)
        return {"status": "No scores available"}
    if not target_metrics:
        return all_score_values(log.results.scores)

    return target_score_values(log.results.scores, target_metrics)


def prepare_log_results(
    log: EvalLog,
    target_metrics: list[InspectScore] | None = None,
    log_progress: bool = True,
) -> list[OutputEntry]:
    """Prepare the results of the Inspect evaluation for CSV export.

    Args:
        log: The Inspect evaluation log.
        target_metrics (optional): The target metrics to include in the results.
        log_progress: Whether to log progress while preparing results.

    Returns:
        The results of the Inspect evaluation for CSV export.

    """
    task_name = log.eval.task
    runtime = get_runtime_from_timestamps(
        log.stats.started_at,
        log.stats.completed_at,
    )
    created = log.eval.created or log.stats.started_at or "N/A"

    results = [
        OutputEntry(
            {
                "Created": created,
                "Run ID": log.eval.task_id,
                "Benchmark": task_name,
                "Metric": score_name,
                "Score": score_value,
                "Runtime (sec)": runtime,
            },
        )
        for score_name, score_value in score_values(log, target_metrics).items()
    ]
    if log_progress:
        status_icon = "✅" if log.status == "success" else "❌"
        logger.info(
            "%s Task: %s | Status: %s | Runtime: %s",
            status_icon,
            task_name,
            log.status,
            runtime,
        )

    return results


def prepare_results(
    log_dir: str | None = None,
    log_files: str | list[str] | None = None,
    logs: EvalLog | list[EvalLog] | None = None,
    tasks: list[str] | None = None,
    target_metrics: dict[str, list[InspectScore]] | None = None,
    task_ids_to_skip: list[str] | None = None,
    export_jsons: bool = False,
    log_progress: bool = True,
    fail_on_log_error: bool = False,
    *,
    progress_callback: ProgressCallback | None = None,
) -> list[OutputEntry]:
    """Prepare the results of the evaluation for CSV export.

    Args:
        log_dir (optional): Path to the directory containing the Inspect logs.
        log_files (optional): Path or list of paths to the Inspect evaluation
            log files.
        logs (optional): The Inspect evaluation log or list of logs.
        tasks (optional): The tasks to include in the results.
        target_metrics (optional): The target metrics to include in the results by task.
        task_ids_to_skip (optional): The task IDs to skip in the results.
        export_jsons (optional): Whether to export the Inspect logs as JSON files.
        log_progress: Whether to log progress while preparing results.
        fail_on_log_error: Whether to reject the results when any log fails to load.
        progress_callback: Optional callback receiving completed task count, total task
            count, and the current task name.

    Returns:
        The results of the evaluation for CSV export.

    Raises:
        ValueError: If no log source is provided or a requested log cannot be
            loaded when strict log-error handling is enabled.

    """
    if not log_dir and not log_files and not logs:
        raise ValueError("At least one of log_dir, log_files, or logs must be provided")
    # Prepare the raw Inspect evaluation logs
    if not logs:
        logs = load_logs(
            log_dir,
            log_files,
            export_jsons,
            fail_on_error=fail_on_log_error,
        )
    if not isinstance(logs, list):
        logs = [logs]
    # Filter the logs by tasks and/or already-exported run IDs
    if tasks:
        existing_tasks = {log.eval.task for log in logs}
        missing_tasks = set(tasks) - existing_tasks
        if missing_tasks:
            logger.warning(
                "Skipping tasks without Inspect logs: %s. Available tasks with "
                "logs: %s",
                missing_tasks,
                existing_tasks,
            )
        logs = [log for log in logs if log.eval.task in tasks]
    if task_ids_to_skip:
        skip_ids = set(task_ids_to_skip)
        logs = [log for log in logs if log.eval.task_id not in skip_ids]
    tasks = [log.eval.task for log in logs]
    if log_progress:
        logger.info(
            "🧮 Preparing results for %s task%s: %s",
            len(tasks),
            "s" if len(tasks) != 1 else "",
            ", ".join(tasks) or "(none)",
        )
    # Prepare the results for CSV export
    results: list[OutputEntry] = []
    for completed, log in enumerate(logs, start=1):
        task_target_metrics: list[InspectScore] | None = (
            target_metrics.get(log.eval.task) if target_metrics else None
        )
        results.extend(
            prepare_log_results(
                log,
                task_target_metrics,
                log_progress=log_progress and progress_callback is None,
            ),
        )
        if progress_callback is not None:
            progress_callback(completed, len(logs), log.eval.task)

    return results


def inspect_existing_results(
    csv_path: str,
    log_progress: bool = True,
) -> tuple[list[str], list[str], list[dict[str, str]], bool]:
    """Inspect existing results and determine whether the header has changed.

    Args:
        csv_path: The path to the output CSV file.
        log_progress: Whether to log progress while inspecting results.

    Returns:
        The fieldnames, existing fieldnames, existing rows, and whether to
        write the header.

    Raises:
        ValueError: If the existing CSV cannot be read or has invalid rows.

    """
    if log_progress:
        logger.info("🔍 Inspecting existing results at %s", csv_path)
    fieldnames = list(get_type_hints(OutputEntry).keys())
    try:
        existing_fieldnames: list[str] = []
        existing_rows: list[dict[str, str]] = []
        should_write_header = True
        # Get existing rows and fieldnames if the file exists
        if Path(csv_path).is_file() and Path(csv_path).stat().st_size > 0:
            with Path(csv_path).open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                existing_fieldnames = list(reader.fieldnames or [])
                existing_rows = list(reader)
                if any(None in row for row in existing_rows):
                    raise ValueError(
                        "Existing CSV contains rows with more values than its header"
                    )
            should_write_header = existing_fieldnames != fieldnames

        return fieldnames, existing_fieldnames, existing_rows, should_write_header
    except (OSError, ValueError, csv.Error) as error:
        raise ValueError(
            f"Failed to inspect existing results at {csv_path}: {error}"
        ) from error


def sanitize_csv_value(value: object) -> object:
    """Prevent spreadsheet applications from interpreting CSV cells as formulas.

    Args:
        value: The CSV cell value to sanitize.

    Returns:
        The original value, or a string prefixed with an apostrophe when it
        begins with a spreadsheet formula marker.
    """
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def format_output_row(row: OutputEntry) -> dict[str, object]:
    """Format an output row for stable CSV presentation.

    Args:
        row: The unformatted CSV output row.

    Returns:
        A copy of the row with numeric score values formatted as strings.
    """
    formatted_row = dict(row)
    score = formatted_row.get("Score")
    if isinstance(score, int | float):
        formatted_row["Score"] = f"{score:.2f}" if score > 1.0 else f"{score:.4f}"

    return {key: sanitize_csv_value(value) for key, value in formatted_row.items()}


def _normalized_existing_rows(
    existing_rows: list[dict[str, str]], fieldnames: list[str]
) -> list[dict[str, object]]:
    """Normalize existing CSV rows to a migrated schema.

    Args:
        existing_rows: Rows read from the existing output file.
        fieldnames: Ordered field names for the migrated output.

    Returns:
        Existing rows with missing values filled and formula-leading strings escaped.
    """
    return [
        {
            fieldname: sanitize_csv_value(
                row.get(fieldname, "N/A") if row.get(fieldname) is not None else "N/A"
            )
            for fieldname in fieldnames
        }
        for row in existing_rows
    ]


def _rewrite_csv_atomically(
    csv_path: str,
    fieldnames: list[str],
    existing_rows: list[dict[str, object]],
    results: list[dict[str, object]],
) -> None:
    """Write a complete CSV to a sibling temporary file and replace the destination.

    Args:
        csv_path: Destination CSV path.
        fieldnames: Ordered output field names.
        existing_rows: Migrated rows from the current destination.
        results: Newly prepared rows to append after the migrated rows.
    """
    destination = Path(csv_path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            writer = csv.DictWriter(
                temporary_file, fieldnames=fieldnames, restval="N/A"
            )
            writer.writeheader()
            writer.writerows(existing_rows)
            writer.writerows(results)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        if destination.exists():
            temporary_path.chmod(stat.S_IMODE(destination.stat().st_mode))
        Path(temporary_path).replace(destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _write_csv_results(
    csv_path: str,
    fieldnames: list[str],
    existing_fieldnames: list[str],
    existing_rows: list[dict[str, str]],
    should_write_header: bool,
    results: list[dict[str, object]],
) -> None:
    """Write prepared results, atomically replacing files that need migration.

    Args:
        csv_path: Destination CSV path.
        fieldnames: Current output field names.
        existing_fieldnames: Field names read from the destination.
        existing_rows: Rows read from the destination.
        should_write_header: Whether the destination needs a new header.
        results: Newly prepared rows to write.

    Raises:
        RuntimeError: If the destination cannot be written safely.
    """
    try:
        if should_write_header:
            migrated_fieldnames = list(dict.fromkeys(fieldnames + existing_fieldnames))
            _rewrite_csv_atomically(
                csv_path,
                migrated_fieldnames,
                _normalized_existing_rows(existing_rows, migrated_fieldnames),
                results,
            )
            return

        with Path(csv_path).open(mode="a", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fieldnames)
            writer.writerows(results)
    except (OSError, ValueError, csv.Error) as error:
        raise RuntimeError(f"Failed to write CSV file: {error}") from error


def export_results(
    log_dir: str | None = None,
    log_files: str | list[str] | None = None,
    logs: EvalLog | list[EvalLog] | None = None,
    tasks: list[str] | None = None,
    target_metrics: dict[str, list[InspectScore]] | None = None,
    csv_path: str | None = None,
    skip_existing: bool = False,
    export_jsons: bool = False,
    log_progress: bool = True,
    fail_on_log_error: bool = False,
    *,
    progress_callback: ProgressCallback | None = None,
) -> int:
    """Export the results of the evaluation to a CSV file.

    Args:
        log_dir (optional): Path to the directory containing the Inspect logs.
        log_files (optional): Path or list of paths to the Inspect evaluation
            log files.
        logs (optional): The Inspect evaluation log or list of logs.
        tasks (optional): The tasks to include in the results.
        target_metrics (optional): The target metrics to include in the results by task.
        csv_path (optional): The path to the output CSV file.
            If not provided, the output will be saved to "brief_results.csv"
            in the current working directory or the log_dir if provided.
        skip_existing (optional): Whether to skip tasks with existing results.
        export_jsons (optional): Whether to export the Inspect logs as JSON files.
        log_progress: Whether to log detailed export progress.
        fail_on_log_error: Whether to reject the export when any log fails to load.
        progress_callback: Optional callback receiving completed task count, total task
            count, and the current task name.

    Returns:
        The number of result rows written to the CSV file.

    Raises:
        ValueError: If inputs or an existing CSV are invalid, or a requested
            log cannot be loaded in strict mode.
    """
    # Prepare the output path
    if not csv_path:
        csv_path = str(Path(log_dir or Path.cwd()) / "brief_results.csv")
    if log_progress:
        logger.info("📦 Gathering results to export to %s", csv_path)
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    # Inspect existing results
    fieldnames, existing_fieldnames, existing_rows, should_write_header = (
        inspect_existing_results(csv_path, log_progress=log_progress)
    )
    if skip_existing and existing_rows and "Run ID" not in existing_fieldnames:
        raise ValueError(
            "Cannot skip existing results: the existing CSV has no 'Run ID' column"
        )
    if skip_existing and any(not row.get("Run ID") for row in existing_rows):
        raise ValueError(
            "Cannot skip existing results: an existing CSV row has no Run ID"
        )
    task_ids_to_skip = [row["Run ID"] for row in existing_rows] if skip_existing else []
    if log_progress:
        logger.info("⏭️ Skipping runs with existing Run IDs: %s", set(task_ids_to_skip))
    # Prepare the results for CSV export
    results = [
        format_output_row(row)
        for row in prepare_results(
            log_dir=log_dir,
            log_files=log_files,
            logs=logs,
            tasks=tasks,
            target_metrics=target_metrics,
            task_ids_to_skip=task_ids_to_skip,
            export_jsons=export_jsons,
            fail_on_log_error=fail_on_log_error,
            log_progress=log_progress,
            progress_callback=progress_callback,
        )
    ]
    _write_csv_results(
        csv_path,
        fieldnames,
        existing_fieldnames,
        existing_rows,
        should_write_header,
        results,
    )
    if log_progress:
        logger.info("📝 Finished writing results to %s", csv_path)
    return len(results)
