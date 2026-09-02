from pathlib import Path

import pytest
from click.utils import strip_ansi
from typer.testing import CliRunner

from inspect_brief import cli


def test_cli_parses_options_and_exports_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    exported: dict[str, object] = {}
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    first_log = tmp_path / "first.eval"
    second_log = tmp_path / "second.eval"
    first_log.touch()
    second_log.touch()
    csv_path = tmp_path / "results" / "brief.csv"

    def export_results(**kwargs: object) -> int:
        exported.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(cli, "export_results", export_results)

    result = CliRunner().invoke(
        cli.app,
        [
            "--log-dir",
            str(log_dir),
            "--log-files",
            f"{first_log}, {second_log}",
            "--tasks",
            "task-a, task-b",
            "--target-metrics",
            '{"task-a":[{"name":"accuracy","is_percentage":false,'
            '"is_higher_better":true,"is_normalized":false}]}',
            "--csv-path",
            str(csv_path),
            "--skip-existing",
        ],
    )

    assert result.exit_code == 0
    progress_callback = exported.pop("progress_callback")
    assert callable(progress_callback)
    assert exported == {
        "log_dir": str(log_dir),
        "log_files": [str(first_log), str(second_log)],
        "tasks": ["task-a", "task-b"],
        "target_metrics": {
            "task-a": [
                {
                    "name": "accuracy",
                    "is_percentage": False,
                    "is_higher_better": True,
                    "is_normalized": False,
                }
            ]
        },
        "csv_path": str(csv_path),
        "skip_existing": True,
        "fail_on_log_error": True,
        "log_progress": False,
    }


@pytest.mark.parametrize(
    ("option_values", "expected_names"),
    [
        pytest.param(["first.eval"], ["first.eval"], id="single"),
        pytest.param(
            ["first.eval", "second.eval"],
            ["first.eval", "second.eval"],
            id="repeated",
        ),
        pytest.param(
            ["first.eval,second.eval", "third.eval"],
            ["first.eval", "second.eval", "third.eval"],
            id="mixed",
        ),
    ],
)
def test_cli_accepts_repeatable_and_comma_separated_log_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    option_values: list[str],
    expected_names: list[str],
) -> None:
    exported: dict[str, object] = {}
    for name in expected_names:
        (tmp_path / name).touch()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(
        cli,
        "export_results",
        lambda **kwargs: exported.update(kwargs) or 0,
    )
    arguments = [item for value in option_values for item in ("--log-files", value)]

    result = CliRunner().invoke(cli.app, arguments)

    assert result.exit_code == 0
    assert exported["log_files"] == [str(tmp_path / name) for name in expected_names]


@pytest.mark.parametrize("path_kind", ["missing", "directory"])
def test_cli_rejects_invalid_log_file_paths(
    tmp_path: Path,
    path_kind: str,
) -> None:
    log_path = tmp_path / path_kind
    if path_kind == "directory":
        log_path.mkdir()

    result = CliRunner().invoke(cli.app, ["--log-files", str(log_path)])

    assert result.exit_code == 2
    normalized_output = " ".join(strip_ansi(result.output).split())
    assert "Invalid value for --log-files" in normalized_output


def test_cli_rejects_empty_log_file_values() -> None:
    result = CliRunner().invoke(cli.app, ["--log-files", " , "])

    assert result.exit_code == 2
    normalized_output = " ".join(strip_ansi(result.output).split())
    assert "Invalid value for --log-files" in normalized_output
    assert "At least one log file path must be provided" in normalized_output


@pytest.mark.parametrize(
    "error",
    [
        ValueError("missing Inspect log"),
        RuntimeError("malformed existing CSV"),
        OSError("cannot write output"),
    ],
)
def test_cli_reports_operational_errors_without_tracebacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: Exception,
) -> None:
    log_path = tmp_path / "input.eval"
    log_path.touch()
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(
        cli,
        "export_results",
        lambda **_: (_ for _ in ()).throw(error),
    )

    result = CliRunner().invoke(cli.app, ["--log-files", str(log_path)])

    assert result.exit_code == 1
    assert result.output == f"Error: {error}\n"
    assert "Traceback" not in result.output
