"""Shared parsing for CLI options and Inspect Hook configuration."""

import json
from pathlib import Path
from typing import get_type_hints

from inspect_brief.core import InspectScore, target_metric_name

_INSPECT_SCORE_KEYS = set(get_type_hints(InspectScore))


def _parse_metric(task: str, index: int, metric: object) -> InspectScore:
    """Validate and construct one configured metric."""
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


def parse_comma_separated(value: str | None) -> list[str] | None:
    """Parse a comma-separated string into non-empty, stripped items.

    Args:
        value: The comma-separated value to parse.

    Returns:
        The parsed items, or None when no non-empty items are provided.
    """
    if value is None:
        return None
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item] or None


def parse_target_metrics(value: str | None) -> dict[str, list[InspectScore]] | None:
    """Parse target-metrics JSON supplied inline or via a file path.

    Args:
        value: A JSON object or a path to a JSON file mapping task names to
            InspectScore definitions.

    Returns:
        The target metrics by task, or None when no value is provided.

    Raises:
        ValueError: If the supplied JSON does not match the expected schema.
    """
    if value is None:
        return None

    path = Path(value)
    raw = path.read_text(encoding="utf-8") if path.is_file() else value
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(
            "Must be a JSON object (or path to one) mapping task name to "
            "InspectScore objects with keys "
            f"{sorted(_INSPECT_SCORE_KEYS)}"
        ) from error

    if not isinstance(parsed, dict):
        raise ValueError("Must be a JSON object mapping task to a list of metrics")

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
