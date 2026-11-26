import json
from pathlib import Path

import pytest

from inspect_brief.parsing import (
    parse_comma_separated,
    parse_log_files,
    parse_target_metrics,
)


def test_parse_comma_separated() -> None:
    assert parse_comma_separated(" first, ,second ") == ["first", "second"]
    assert parse_comma_separated(["first, second", "third"]) == [
        "first",
        "second",
        "third",
    ]
    assert parse_comma_separated(None) is None


def test_parse_log_files_normalizes_and_resolves_paths(tmp_path: Path) -> None:
    first_log = tmp_path / "first.eval"
    second_log = tmp_path / "second.json"
    first_log.touch()
    second_log.touch()

    assert parse_log_files([f"{first_log}, {second_log}"]) == [
        str(first_log),
        str(second_log),
    ]


def test_parse_log_files_preserves_filesystem_uris(tmp_path: Path) -> None:
    local_log = tmp_path / "local.json"
    local_log.touch()

    assert parse_log_files(["s3://bucket/remote.json", local_log.as_uri()]) == [
        "s3://bucket/remote.json",
        local_log.as_uri(),
    ]


def test_parse_log_files_rejects_empty_values() -> None:
    with pytest.raises(ValueError, match="At least one log file path"):
        parse_log_files([" ", " , "])


def test_parse_log_files_rejects_unsupported_files(tmp_path: Path) -> None:
    log_path = tmp_path / "run.txt"
    log_path.touch()

    with pytest.raises(ValueError, match=r"supports only \.eval and \.json log files"):
        parse_log_files([str(log_path)])

    with pytest.raises(ValueError, match=r"supports only \.eval and \.json log files"):
        parse_log_files(["s3://bucket/run.txt"])


def test_parse_log_files_rejects_uri_credentials() -> None:
    for source in (
        "https://alice:password@example.com/run.eval",
        "https://example.com/run.eval?token=secret",
    ):
        with pytest.raises(ValueError, match="does not accept a log URI"):
            parse_log_files([source])


def test_parse_log_files_excludes_invalid_authority_from_the_error() -> None:
    with pytest.raises(ValueError, match=r"^Invalid log source URI$"):
        parse_log_files(["https://placeholder@example\N{FULLWIDTH COLON}443/run.eval"])


def test_parse_target_metrics_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match="inline value 'not json' is not valid JSON"):
        parse_target_metrics("not json")


def test_parse_target_metrics_identifies_invalid_json_file(tmp_path: Path) -> None:
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text("not json", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match=r"INSPECT_BRIEF_TARGET_METRICS file '.+metrics\.json' is not valid JSON",
    ):
        parse_target_metrics(str(metrics_path), source="INSPECT_BRIEF_TARGET_METRICS")


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
            },
        ],
    }


@pytest.mark.parametrize(
    ("metric", "message"),
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
def test_parse_target_metrics_rejects_invalid_field_types(
    metric: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_target_metrics(json.dumps({"task-a": [metric]}))


def test_parse_target_metrics_rejects_duplicate_output_labels() -> None:
    with pytest.raises(
        ValueError,
        match=r"duplicates the output label.*distinct 'label'",
    ):
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
                    ],
                },
            ),
        )


def test_parse_target_metrics_accepts_score_name_and_label() -> None:
    metric = {
        "name": "accuracy",
        "is_percentage": True,
        "is_higher_better": False,
        "is_normalized": True,
        "score_name": "security",
        "label": "attack success rate",
    }

    assert parse_target_metrics(json.dumps({"task-a": [metric]})) == {
        "task-a": [metric],
    }


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"score_name": ""}, r"non-empty strings for \['score_name'\]"),
        ({"label": " "}, r"non-empty strings for \['label'\]"),
        (
            {"score_name": 1, "label": None},
            r"non-empty strings for \['label', 'score_name'\]",
        ),
        ({"scorer": "utility"}, "and optionally"),
        ({"unit": "ms"}, "and optionally"),
    ],
)
def test_parse_target_metrics_rejects_invalid_optional_keys(
    extra: dict[str, object],
    message: str,
) -> None:
    metric = {
        "name": "accuracy",
        "is_percentage": False,
        "is_higher_better": True,
        "is_normalized": False,
    } | extra

    with pytest.raises(ValueError, match=message):
        parse_target_metrics(json.dumps({"task-a": [metric]}))


def test_parse_target_metrics_checks_labels_after_relabelling(tmp_path: Path) -> None:
    base = {
        "name": "accuracy",
        "is_percentage": True,
        "is_higher_better": True,
        "is_normalized": True,
    }
    distinct = [base | {"score_name": "utility"}, base | {"score_name": "security"}]
    relabelled = [
        base | {"score_name": "utility", "label": "utility"},
        base | {"score_name": "security", "label": "security"},
    ]

    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps({"task-a": distinct}), encoding="utf-8")
    with pytest.raises(
        ValueError,
        match=r"duplicates the output label.*distinct 'label'",
    ):
        parse_target_metrics(str(metrics_path))

    metrics_path.write_text(json.dumps({"task-a": relabelled}), encoding="utf-8")
    assert parse_target_metrics(str(metrics_path)) == {"task-a": relabelled}


def test_parse_target_metrics_strips_optional_values() -> None:
    metric = {
        "name": "accuracy",
        "is_percentage": True,
        "is_higher_better": True,
        "is_normalized": True,
        "score_name": " utility ",
        "label": "  padded  ",
    }

    parsed = parse_target_metrics(json.dumps({"task-a": [metric]}))
    assert parsed is not None
    [score] = parsed["task-a"]
    assert (score["score_name"], score["label"]) == ("utility", "padded")
