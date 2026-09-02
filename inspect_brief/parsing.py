"""Shared parsing for CLI options and Inspect Hook configuration."""

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import get_type_hints

from inspect_brief.core import (
    InspectScore,
    _eval_log_source_key,
    _is_log_uri,
    target_metric_name,
)

_INSPECT_SCORE_KEYS = set(get_type_hints(InspectScore))


def _parse_metric(task: str, index: int, metric: object) -> InspectScore:
    """Validate and construct one configured metric.

    Args:
        task: Task name that owns the metric configuration.
        index: Position of the metric in the task configuration.
        metric: Unvalidated metric value decoded from JSON.

    Returns:
        A validated Inspect score definition.

    Raises:
        ValueError: If the metric does not match the required schema.

    """
    if not isinstance(metric, dict) or set(metric) != _INSPECT_SCORE_KEYS:
        raise ValueError(
            f"Metric {index} for task '{task}' must be an object with exactly "
            f"keys {sorted(_INSPECT_SCORE_KEYS)}"
        )
    if not isinstance(metric["name"], str):
        raise ValueError(f"Metric {index} for task '{task}' must have a string name")
    boolean_keys = _INSPECT_SCORE_KEYS - {"name"}
    invalid_boolean_keys = [
        key for key in boolean_keys if type(metric[key]) is not bool
    ]
    if invalid_boolean_keys:
        raise ValueError(
            f"Metric {index} for task '{task}' must use JSON booleans for "
            f"{sorted(invalid_boolean_keys)}"
        )
    return InspectScore(
        name=metric["name"],
        is_percentage=metric["is_percentage"],
        is_higher_better=metric["is_higher_better"],
        is_normalized=metric["is_normalized"],
    )


def parse_comma_separated(
    value: str | Iterable[str] | None,
) -> list[str] | None:
    """Parse comma-separated values into non-empty, stripped items.

    Args:
        value: One or more comma-separated values to parse.

    Returns:
        The parsed items, or None when no non-empty items are provided.

    """
    if value is None:
        return None
    values = [value] if isinstance(value, str) else value
    items = [item.strip() for entry in values for item in entry.split(",")]
    return [item for item in items if item] or None


def parse_log_files(values: Iterable[str] | None) -> list[str] | None:
    """Normalize repeatable log-file values and validate each source.

    Args:
        values: Raw values, each of which may contain comma-separated paths or
            filesystem URIs.

    Returns:
        Resolved local paths and unchanged filesystem URIs, or None when no
        sources are supplied.

    Raises:
        ValueError: If no sources remain after normalization, or a source is
            invalid.
    """
    if values is None:
        return None

    log_files = parse_comma_separated(values)
    if log_files is None:
        raise ValueError("At least one log file path must be provided.")

    resolved_log_files: list[str] = []
    for value in log_files:
        if _is_log_uri(value):
            _eval_log_source_key(value)
            resolved_log_files.append(value)
            continue

        path = Path(value)
        if path.suffix != ".eval":
            raise ValueError("Inspect Brief currently supports only .eval log files")
        if not path.exists():
            raise ValueError(f"File {value!r} does not exist.")
        if not path.is_file():
            raise ValueError(f"Path {value!r} is not a file.")
        if not os.access(path, os.R_OK):
            raise ValueError(f"File {value!r} is not readable.")
        resolved_log_files.append(str(path.resolve()))
    return resolved_log_files


def parse_target_metrics(
    value: str | None,
    source: str = "target metrics",
) -> dict[str, list[InspectScore]] | None:
    """Parse target-metrics JSON supplied inline or via a file path.

    Args:
        value: A JSON object or a path to a JSON file mapping task names to
            InspectScore definitions.
        source: A user-facing description of where the value came from.

    Returns:
        The target metrics by task, or None when no value is provided.

    Raises:
        ValueError: If the supplied JSON does not match the expected schema.

    """
    if value is None:
        return None

    path = Path(value)
    is_file = path.is_file()
    try:
        raw = path.read_text(encoding="utf-8") if is_file else value
    except OSError as error:
        raise ValueError(f"Could not read {source} file {value!r}: {error}") from error

    input_description = f"file {value!r}" if is_file else f"inline value {value!r}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{source} {input_description} is not valid JSON. Expected a JSON "
            "object mapping task names to InspectScore objects with keys "
            f"{sorted(_INSPECT_SCORE_KEYS)}"
        ) from error

    if not isinstance(parsed, dict):
        raise ValueError(
            f"{source} {input_description} must be a JSON object mapping task "
            "names to lists of metrics"
        )

    result: dict[str, list[InspectScore]] = {}
    for task, metrics in parsed.items():
        if not isinstance(task, str) or not isinstance(metrics, list):
            raise ValueError(
                f"Task '{task}' must map to a list of InspectScore objects"
            )
        scores: list[InspectScore] = []
        metric_labels: dict[str, int] = {}
        for index, metric in enumerate(metrics):
            score = _parse_metric(task, index, metric)
            label = target_metric_name(score)
            if label in metric_labels:
                raise ValueError(
                    f"Metric {index} for task '{task}' duplicates the output label "
                    f"of metric {metric_labels[label]}: {label!r}"
                )
            metric_labels[label] = index
            scores.append(score)
        result[task] = scores

    return result
