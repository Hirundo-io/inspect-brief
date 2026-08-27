import json
from pathlib import Path

import pytest

from inspect_brief.parsing import parse_comma_separated, parse_target_metrics


def test_parse_comma_separated() -> None:
    assert parse_comma_separated(" first, ,second ") == ["first", "second"]
    assert parse_comma_separated(None) is None


def test_parse_target_metrics_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="Must be a JSON object"):
        parse_target_metrics("not json")


def test_parse_target_metrics_reads_json_file(tmp_path: Path) -> None:
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(
        '{"task-a":[{"name":"accuracy","is_percentage":false,'
        '"is_higher_better":true,"is_normalized":false}]}',
        encoding="utf-8",
    )

    assert parse_target_metrics(str(metrics_path)) == {
        "task-a": [
            {
                "name": "accuracy",
                "is_percentage": False,
                "is_higher_better": True,
                "is_normalized": False,
            }
        ]
    }


@pytest.mark.parametrize(
    "metric, message",
    [
        (
            {
                "name": 1,
                "is_percentage": False,
                "is_higher_better": True,
                "is_normalized": False,
            },
            "string name",
        ),
        (
            {
                "name": "accuracy",
                "is_percentage": "false",
                "is_higher_better": True,
                "is_normalized": False,
            },
            "JSON booleans",
        ),
    ],
)
def test_parse_target_metrics_rejects_invalid_field_types(metric, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_target_metrics(json.dumps({"task-a": [metric]}))


def test_parse_target_metrics_rejects_duplicate_output_labels() -> None:
    with pytest.raises(ValueError, match="duplicates the output label"):
        parse_target_metrics(
            json.dumps(
                {
                    "task-a": [
                        {
                            "name": "accuracy",
                            "is_percentage": True,
                            "is_higher_better": True,
                            "is_normalized": False,
                        },
                        {
                            "name": "accuracy",
                            "is_percentage": True,
                            "is_higher_better": True,
                            "is_normalized": True,
                        },
                    ]
                }
            )
        )
