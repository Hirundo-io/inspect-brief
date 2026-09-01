from pathlib import Path

import pytest
from typer.testing import CliRunner

from inspect_brief import cli


def test_cli_parses_options_and_exports_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    exported: dict[str, object] = {}
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
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
            "first.eval, second.eval",
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
    assert exported == {
        "log_dir": str(log_dir),
        "log_files": ["first.eval", "second.eval"],
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
    }


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
    error: Exception,
) -> None:
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(
        cli,
        "export_results",
        lambda **_: (_ for _ in ()).throw(error),
    )

    result = CliRunner().invoke(cli.app, ["--log-files", "missing.eval"])

    assert result.exit_code == 1
    assert result.output == f"Error: {error}\n"
    assert "Traceback" not in result.output
