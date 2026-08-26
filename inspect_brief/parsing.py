"""Shared parsing for CLI options and Inspect Hook configuration."""

import json
from pathlib import Path
from typing import get_type_hints

from inspect_brief.core import InspectScore

_INSPECT_SCORE_KEYS = set(get_type_hints(InspectScore))


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
        for index, metric in enumerate(metrics):
            if not isinstance(metric, dict) or set(metric) != _INSPECT_SCORE_KEYS:
                raise ValueError(
                    f"Metric {index} for task '{task}' must be an object with exactly "
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
