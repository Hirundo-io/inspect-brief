"""Load Inspect logs and export their metric summaries to CSV."""

import csv
import logging
import os
import re
import stat
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal, NotRequired, Protocol, TypedDict, get_type_hints
from urllib.parse import SplitResult, urlsplit, urlunsplit
from urllib.request import url2pathname

from fsspec.core import url_to_fs
from inspect_ai.log import (
    EvalLog,
    EvalScore,
    list_eval_logs,
    read_eval_log,
    write_eval_log,
)

logger = logging.getLogger(__name__)

_SUPPORTED_LOG_FORMATS: tuple[Literal["eval", "json"], ...] = ("eval", "json")
_SUPPORTED_LOG_SUFFIXES = tuple(
    f".{log_format}" for log_format in _SUPPORTED_LOG_FORMATS
)
_SUPPORTED_LOG_ERROR = "Inspect Brief supports only .eval and .json log files"
_URI_CREDENTIALS_ERROR = (
    "Inspect Brief does not accept a log URI carrying credentials, a query string, "
    "or a fragment: Inspect and its storage backends record the locations they "
    "read and list in their own diagnostics, where Inspect Brief cannot redact "
    "them. Supply provider credentials through the environment instead."
)
# An explicit scheme after fsspec's separator starts another URI.
_URI_CHAIN_SEPARATOR: re.Pattern[str] = re.compile(r"::(?=[a-zA-Z][a-zA-Z0-9+.-]*://)")
# adlfs uses the text before @ as the container on public Azure storage hosts.
_AZURE_CONTAINER_URI: re.Pattern[str] = re.compile(
    r"(?:az|abfss?)://"
    r"(?=[a-z0-9-]{3,63}@)[a-z0-9]+(?:-[a-z0-9]+)*@"
    r"[a-z0-9]{3,24}\.(?:blob|dfs)\.core\.windows\.net(?=[/?#]|\Z)",
)


def _format_rank(log_source: str) -> int:
    """Rank a log source by how close its format is to Inspect's native one.

    Args:
        log_source: Local path or filesystem URI for an evaluation log.

    Returns:
        The position of the source's format in ``_SUPPORTED_LOG_SUFFIXES``, so
        ``.eval`` outranks ``.json``, or a trailing rank for any other source.

    """
    suffix = PurePosixPath(urlsplit(log_source).path).suffix
    if suffix in _SUPPORTED_LOG_SUFFIXES:
        return _SUPPORTED_LOG_SUFFIXES.index(suffix)
    return len(_SUPPORTED_LOG_SUFFIXES)


def validate_log_suffix(suffix: str) -> None:
    """Reject a log source that is not in a supported Inspect log format.

    Args:
        suffix: File suffix of the log source, including the leading dot.

    Raises:
        ValueError: If the suffix is not a supported Inspect log format.

    """
    if suffix not in _SUPPORTED_LOG_SUFFIXES:
        raise ValueError(_SUPPORTED_LOG_ERROR)


class ProgressCallback(Protocol):
    """Receive progress updates while Inspect logs are prepared."""

    def __call__(
        self,
        completed_log_count: int,
        total_log_count: int,
        current_task_name: str,
        /,
    ) -> None:
        """Report the current log count and task name."""


class InspectScore(TypedDict):
    """Score definition for inspect-ai.

    Args:
        name: The name of the score metric in the inspect-ai logs.
        is_percentage: Whether the score is a percentage.
        is_higher_better: Whether a higher value is better.
        is_normalized: Whether the score is normalized. Relevant only for
            percentage scores.
        score_name: Optional. Read the metric only from the score with this
            name (``EvalScore.name``, not ``EvalScore.scorer``), for logs
            where several scores report a metric with the same name.
        label: Optional. The metric label written to the CSV in place of
            ``name``; the percentage and direction annotations are still
            added.

    """

    name: str
    is_percentage: bool
    is_higher_better: bool
    is_normalized: bool
    score_name: NotRequired[str]
    label: NotRequired[str]


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
    log_dir: str,
    load_errors: list[tuple[str, Exception]],
) -> list[str]:
    """Recursively discover evaluation-log sources while recording scan failures.

    Discovery defers to Inspect's own listing, which accepts `.eval` logs by
    suffix but requires Inspect's timestamped log name for other formats. Files
    that merely sit beside the logs, such as an eval-set manifest or a
    target-metrics file, are therefore left alone. A directory URI carrying
    credentials is rejected by ``_reject_uri_credentials`` before Inspect sees it.

    Args:
        log_dir: Directory to scan recursively for evaluation logs.
        load_errors: Mutable collection that receives directory scan failures.

    Returns:
        Discovered log sources ordered by format, so a run stored in both
        formats is read from its native `.eval` log, or an empty list when
        scanning fails.

    """
    # Past this guard `log_dir` holds nothing that needs redacting.
    _reject_uri_credentials(log_dir)
    try:
        log_infos = list_eval_logs(log_dir, formats=list(_SUPPORTED_LOG_FORMATS))
    # Inspect lists through fsspec, whose backends raise provider-specific errors.
    except Exception as error:
        logger.warning(
            "❌ Could not scan Inspect log directory %s",
            log_dir,
            exc_info=error,
        )
        load_errors.append((f"{log_dir} (directory scan)", error))
        return []

    # A stable sort keeps Inspect's ordering within each format.
    sources = sorted((log_info.name for log_info in log_infos), key=_format_rank)
    if not sources:
        logger.warning(
            "⚠️ No Inspect logs found in %s. A %s log is discovered only when its "
            "name follows Inspect's timestamped log naming; pass it through "
            "--log-files instead if it is a log.",
            log_dir,
            ", ".join(_SUPPORTED_LOG_SUFFIXES[1:]),
        )
    return sources


def _resolve_log_path(log_file: str) -> Path:
    """Validate and resolve a local Inspect evaluation-log path.

    Unsupported log formats are rejected by ``validate_log_suffix``.

    Args:
        log_file: Path supplied to the log loader.

    Returns:
        The canonical path to the `.eval` or `.json` log.
    """
    path = Path(log_file)
    validate_log_suffix(path.suffix)
    return path.resolve(strict=True)


def _parse_log_uri(log_file: str) -> SplitResult | None:
    """Parse a log URI without exposing invalid input in errors.

    Args:
        log_file: Log source supplied to the loader.

    Returns:
        The parsed URI, or None for a local path.

    Raises:
        ValueError: If the URI cannot be parsed.
    """
    try:
        parsed = urlsplit(log_file)
    except ValueError:
        # urlsplit may quote the unredacted authority in the original exception.
        raise ValueError("Invalid log source URI") from None
    if parsed.scheme and (parsed.scheme == "file" or "://" in log_file):
        return parsed
    return None


def _file_uri_to_path(log_file: str) -> Path:
    """Convert a local ``file:`` URI to the path it names.

    Links are not followed and the path need not exist, so the result still
    describes a dangling symlink rather than its missing target.

    Args:
        log_file: Local file URI supplied to the log loader.

    Returns:
        The filesystem path the URI names.
    """
    parsed = urlsplit(log_file)
    authority = "" if parsed.netloc in {"", "localhost"} else f"//{parsed.netloc}"
    return Path(url2pathname(f"{authority}{parsed.path}"))


def _local_log_path(log_file: str) -> Path | None:
    """Return the filesystem path a local log source names.

    Args:
        log_file: Local path or filesystem URI supplied to the loader.

    Returns:
        The path named by a local path or ``file:`` URI, without following
        links, or None for a source held by a remote provider.
    """
    parsed = _parse_log_uri(log_file)
    if parsed is None:
        return Path(log_file)
    if parsed.scheme != "file":
        return None
    return _file_uri_to_path(log_file)


def _resolve_file_uri_path(log_file: str) -> Path:
    """Resolve a local ``file:`` URI to its canonical filesystem path.

    Args:
        log_file: Local file URI supplied to the log loader.

    Returns:
        The canonical path to the referenced `.eval` or `.json` log.
    """
    return _resolve_log_path(str(_file_uri_to_path(log_file)))


def _redact_log_source(log_file: str) -> str:
    """Remove credentials from every URI in a log source.

    Args:
        log_file: Local path or filesystem URI supplied to the loader.

    Returns:
        The source as supplied if clean, a stripped URI, or a placeholder for
        a sensitive fsspec chain.
    """
    components = _URI_CHAIN_SEPARATOR.split(log_file)
    for component in components:
        parsed = _parse_log_uri(component)
        if parsed is None:
            continue
        netloc = parsed.netloc
        has_credential_userinfo = (
            "@" in netloc and _AZURE_CONTAINER_URI.match(component) is None
        )
        if not (has_credential_userinfo or "?" in component or "#" in component):
            continue
        if len(components) > 1:
            return "<redacted log URI>"
        if has_credential_userinfo:
            netloc = netloc.rpartition("@")[2]
        return urlunsplit(parsed._replace(netloc=netloc, query="", fragment=""))
    return log_file


def _display_log_source(log_file: str) -> str:
    """Return a redacted log source, using a placeholder if parsing fails.

    Args:
        log_file: Local path or filesystem URI supplied to the loader.

    Returns:
        The safe source label for diagnostics.
    """
    try:
        return _redact_log_source(log_file)
    except ValueError:
        return "<invalid log source>"


def _reject_uri_credentials(log_location: str) -> None:
    """Reject a log location whose URI carries credentials.

    Inspect and fsspec log the locations they read and list at DEBUG before
    Inspect Brief sees any result, so a URI carrying credentials must never
    reach them.

    Args:
        log_location: Local path or URI of a log or log directory.

    Raises:
        ValueError: If the URI is malformed or carries credentials, a query string,
            or a fragment.
    """
    if _redact_log_source(log_location) != log_location:
        raise ValueError(_URI_CREDENTIALS_ERROR)


def _log_source_key(log_file: str) -> str | Path:
    """Validate a log source and return its stable deduplication key.

    Unsupported log formats are rejected by ``validate_log_suffix``, and URIs
    carrying credentials by ``_reject_uri_credentials``.

    Args:
        log_file: Local path or filesystem URI supplied to the loader.

    Returns:
        The unchanged URI for remote/provider sources, or the canonical local path.
    """
    _reject_uri_credentials(log_file)
    parsed = _parse_log_uri(log_file)
    if parsed is not None:
        if parsed.scheme == "file":
            return _resolve_file_uri_path(log_file)
        validate_log_suffix(PurePosixPath(parsed.path).suffix)
        return log_file

    return _resolve_log_path(log_file)


def _is_json_log_source(source_key: str | Path) -> bool:
    """Return whether a validated log source is already JSON formatted.

    Args:
        source_key: Canonical local path or unchanged provider URI.

    Returns:
        True when the source path has a `.json` suffix.
    """
    if isinstance(source_key, Path):
        return source_key.suffix == ".json"
    return PurePosixPath(urlsplit(source_key).path).suffix == ".json"


def _json_sidecar_path(log_file: str) -> str | Path:
    """Return the JSON sidecar location for a local path or filesystem URI.

    Args:
        log_file: Local path or filesystem URI for an Inspect evaluation log.

    Returns:
        A sibling location with a `.json` suffix.
    """
    parsed = _parse_log_uri(log_file)
    if parsed is not None:
        return urlunsplit(
            parsed._replace(path=str(PurePosixPath(parsed.path).with_suffix(".json"))),
        )

    return Path(log_file).with_suffix(".json")


def _provider_object_key(log_uri: str) -> tuple[str, str] | None:
    """Identify the stored object that a provider URI names.

    Two URIs can spell one object differently, such as ``memory://logs/run.json``
    and ``memory:///logs/run.json``, so comparing the URIs as text is not enough.
    Resolution goes through the same fsspec backend that Inspect reads and
    writes with, which strips each URI to the backend's own path for the object.

    Args:
        log_uri: Provider URI for an Inspect evaluation log.

    Returns:
        The backend protocol paired with the object path, or None when the
        backend cannot be resolved.
    """
    try:
        filesystem, object_path = url_to_fs(log_uri)
    # fsspec raises backend-specific errors, and an uninstalled backend raises
    # ImportError, so no narrower exception type covers resolution failures.
    except Exception:  # ruff: ignore[blind-except]
        return None
    protocol = filesystem.protocol
    if not isinstance(protocol, str):
        protocol = protocol[0]
    return (protocol, object_path)


def _sidecar_overwrites_input(
    destination: str | Path,
    source_keys: set[str | Path],
) -> bool:
    """Check a sidecar destination against all validated input locations.

    Args:
        destination: Local path or provider URI of the proposed sidecar.
        source_keys: Canonical local paths and provider URIs of the inputs.

    Returns:
        Whether writing the sidecar would reach an input, including through
        local hardlinks and symlinks.
    """
    try:
        destination_key = _log_source_key(str(destination))
    except FileNotFoundError:
        # A dangling symlink resolves to nothing, but writing to it still
        # follows the link and creates or overwrites whatever it points at.
        # Inspect reports discovered local logs as file: URIs, so the
        # destination has to be read back as a path before the link is checked.
        local_destination = _local_log_path(str(destination))
        return local_destination is not None and local_destination.is_symlink()
    if destination_key in source_keys:
        return True
    if isinstance(destination_key, Path):
        return any(
            destination_key.samefile(source)
            for source in source_keys
            if isinstance(source, Path)
        )
    destination_object = _provider_object_key(destination_key)
    for source in source_keys:
        if not isinstance(source, str):
            continue
        source_object = _provider_object_key(source)
        # An unresolvable backend leaves the two sources indistinguishable, so
        # the sidecar is skipped rather than risked.
        if source_object is None or source_object == destination_object:
            return True
    return destination_object is None


def _export_log_json(
    log: EvalLog,
    log_file: str,
    source_keys: set[str | Path],
) -> None:
    """Export a JSON sidecar beside an Inspect evaluation log.

    Args:
        log: Inspect evaluation log to export.
        log_file: Validated source location of the evaluation log.
        source_keys: Validated input locations that sidecars must not overwrite.
    """
    logger.info("📝 Exporting Inspect log %s as JSON", log_file)
    try:
        destination = _json_sidecar_path(log_file)
        if _sidecar_overwrites_input(destination, source_keys):
            logger.warning(
                "Skipping JSON sidecar for %s: destination is a symlink or "
                "aliases an input log",
                log_file,
            )
            return
        write_eval_log(log, destination, format="json")
    except Exception:  # ruff: ignore[blind-except] -- Inspect writers expose no common error type.
        logger.warning("❌ Could not export Inspect log %s", log_file)


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
        log_dir (optional): Path or URI of the directory containing the Inspect
            logs. URIs with credentials, query strings, or fragments are rejected
            before storage backends can log them. Standard Azure Blob and Data Lake
            URIs may include the container name before the account host.
        log_files (optional): Path or list of paths to the Inspect evaluation
            log files. The same URI restrictions apply as for ``log_dir``.
        export_jsons (optional): Whether to export the Inspect logs as JSON sidecars.
            Sidecars whose destinations alias any input are skipped.
        fail_on_error: Whether to raise after processing all paths if any log
            fails to load.

    Returns:
        The list of Inspect evaluation logs.

    Raises:
        ValueError: If no input is provided, if ``log_dir`` is a URI carrying
            credentials, or if ``fail_on_error`` is true and one or more logs
            cannot be loaded.

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
    sidecars: list[tuple[EvalLog, str]] = []
    source_keys: set[str | Path] = set()
    for log_file in paths:
        display_source = _display_log_source(log_file)
        try:
            # Canonicalize local paths for deduplication while preserving provider
            # URIs so Inspect's fsspec-backed reader can handle them unchanged.
            source_key = _log_source_key(log_file)
            if source_key in source_keys:
                continue
            source_keys.add(source_key)
            read_source = log_file if isinstance(source_key, str) else str(source_key)
            log = read_eval_log(read_source)
            logs.append(log)
            if export_jsons and not _is_json_log_source(source_key):
                sidecars.append((log, log_file))
        # Inspect log readers and storage backends may raise provider-specific
        # exceptions. Isolate each path so one malformed log does not stop the rest.
        except Exception as error:  # ruff: ignore[blind-except]
            logger.warning(
                "❌ Could not load Inspect log %s: %s",
                display_source,
                error,
            )
            load_errors.append((display_source, error))

    # Read every input before exporting so later inputs, including unreadable
    # logs, are protected from earlier logs' sidecar destinations.
    for log, log_file in sidecars:
        _export_log_json(log, log_file, source_keys)

    if load_errors and fail_on_error:
        failed_paths = ", ".join(path for path, _ in load_errors)
        raise ValueError(
            f"Could not load {len(load_errors)} Inspect log(s): {failed_paths}",
        ) from load_errors[0][1]

    return logs


def target_metric_name(target_metric: InspectScore) -> str:
    """Format a configured metric name for CSV output.

    Args:
        target_metric: The configured metric definition.

    Returns:
        The metric name annotated with percentage and direction metadata.

    """
    metric_name = target_metric.get("label", target_metric["name"])
    if target_metric["is_percentage"]:
        metric_name += " (%)"

    return metric_name + (" ⬆️" if target_metric["is_higher_better"] else " ⬇️")


def failed_score_values(
    status: str,
    target_metrics: list[InspectScore] | None,
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
    scores: list[EvalScore],
    target_metrics: list[InspectScore],
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
        score_name = target_metric.get("score_name")
        for score in scores:
            if score_name is not None and score.name != score_name:
                continue
            if target_metric["name"] not in score.metrics:
                continue
            metric_value = score.metrics[target_metric["name"]].value
            if target_metric["is_percentage"] and target_metric["is_normalized"]:
                metric_value *= 100.0
            score_values[metric_name] = metric_value
            break
        else:
            if score_name is None:
                message = f"Metric '{target_metric['name']}' not found"
            elif any(score.name == score_name for score in scores):
                message = (
                    f"Metric '{target_metric['name']}' not found in score "
                    f"'{score_name}'"
                )
            else:
                message = f"Score '{score_name}' not found"
            score_values[metric_name] = message

    return score_values


def score_values(
    log: EvalLog,
    target_metrics: list[InspectScore] | None,
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


def _attempt_key(log: EvalLog) -> tuple[str, str] | None:
    """Identify the evaluation attempt that a log records.

    Args:
        log: A loaded Inspect evaluation log.

    Returns:
        The log's run and task identifiers, or None when it carries no task
        identifier to match other logs on.

    """
    if not log.eval.task_id:
        return None
    return (log.eval.run_id, log.eval.task_id)


def _deduplicate_runs(logs: list[EvalLog], log_progress: bool) -> list[EvalLog]:
    """Keep one log per evaluation attempt.

    A single attempt can reach the loader more than once, most often as an
    ``.eval`` log and the ``.json`` sidecar exported beside it. Both decode to
    the same attempt, so only one is kept: the copy in Inspect's native
    ``.eval`` format wins wherever an attempt arrives in both formats, whether
    the copies come from one directory scan or from explicit log files, and the
    attempt stays at the position where it first arrived.

    Attempts are identified by run ID and task ID together. Inspect reuses the
    task ID when it retries a run and gives the retry its own run ID, so
    retries stay distinct while copies of one attempt collapse.

    Args:
        logs: The loaded Inspect evaluation logs, in load order.
        log_progress: Whether to log the dropped duplicates.

    Returns:
        The logs without duplicate copies of an already-seen attempt.

    """
    unique_logs: list[EvalLog] = []
    positions: dict[tuple[str, str], int] = {}
    duplicate_task_ids: list[str] = []
    for log in logs:
        attempt = _attempt_key(log)
        if attempt is None:
            unique_logs.append(log)
            continue
        position = positions.get(attempt)
        if position is None:
            positions[attempt] = len(unique_logs)
            unique_logs.append(log)
            continue
        duplicate_task_ids.append(log.eval.task_id)
        if _format_rank(log.location) < _format_rank(unique_logs[position].location):
            unique_logs[position] = log

    if duplicate_task_ids and log_progress:
        logger.info(
            "⏭️ Skipping %s duplicate log%s of already-loaded Run IDs: %s",
            len(duplicate_task_ids),
            "s" if len(duplicate_task_ids) != 1 else "",
            ", ".join(sorted(set(duplicate_task_ids))),
        )

    return unique_logs


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
        progress_callback: Optional callback receiving completed log count, total log
            count, and the current task name.

    Returns:
        The results of the evaluation for CSV export.

    Raises:
        ValueError: If no log source is provided, ``log_dir`` is a URI
            carrying credentials, or a requested log cannot be loaded when
            strict log-error handling is enabled.

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
    # One evaluation run can arrive more than once, such as an .eval log and the
    # .json sidecar beside it, which would otherwise be reported twice.
    logs = _deduplicate_runs(logs, log_progress)
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
    total_log_count = len(logs)
    for completed_log_count, log in enumerate(logs, start=1):
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
            progress_callback(completed_log_count, total_log_count, log.eval.task)

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
                        "Existing CSV contains rows with more values than its header",
                    )
            should_write_header = existing_fieldnames != fieldnames

        return fieldnames, existing_fieldnames, existing_rows, should_write_header
    except (OSError, ValueError, csv.Error) as error:
        raise ValueError(
            f"Failed to inspect existing results at {csv_path}: {error}",
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


def desanitize_csv_value(value: str) -> str:
    """Recover the identity of a CSV cell written by `sanitize_csv_value`.

    Args:
        value: A CSV cell value as stored on disk.

    Returns:
        The value without the apostrophe that `sanitize_csv_value` prepends to
        spreadsheet formula markers.
    """
    if value.startswith("'") and value[1:2] in {"=", "+", "-", "@"}:
        return value[1:]
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
    existing_rows: list[dict[str, str]],
    fieldnames: list[str],
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
                row.get(fieldname, "N/A") if row.get(fieldname) is not None else "N/A",
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
                temporary_file,
                fieldnames=fieldnames,
                restval="N/A",
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
        progress_callback: Optional callback receiving completed log count, total log
            count, and the current task name.

    Returns:
        The number of result rows written to the CSV file.

    Raises:
        ValueError: If inputs or an existing CSV are invalid, or a requested
            log cannot be loaded in strict mode.
    """
    # Prepare the output path
    if not csv_path:
        if log_dir:
            _reject_uri_credentials(log_dir)
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
            "Cannot skip existing results: the existing CSV has no 'Run ID' column",
        )
    if skip_existing and any(not row.get("Run ID") for row in existing_rows):
        raise ValueError(
            "Cannot skip existing results: an existing CSV row has no Run ID",
        )
    # Stored Run IDs carry the sanitization applied on write, so compare identity
    # rather than the display value.
    task_ids_to_skip = (
        [desanitize_csv_value(row["Run ID"]) for row in existing_rows]
        if skip_existing
        else []
    )
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
