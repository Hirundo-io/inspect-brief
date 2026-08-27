from typer.testing import CliRunner

from inspect_brief import cli


def test_cli_parses_options_and_exports_results(monkeypatch) -> None:
    exported: dict[str, object] = {}

    def export_results(**kwargs) -> int:
        exported.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(cli, "export_results", export_results)

    result = CliRunner().invoke(
        cli.app,
        [
            "--log-dir",
            "logs",
            "--log-files",
            "first.eval, second.eval",
            "--tasks",
            "task-a, task-b",
            "--target-metrics",
            '{"task-a":[{"name":"accuracy","is_percentage":false,'
            '"is_higher_better":true,"is_normalized":false}]}',
            "--csv-path",
            "results/brief.csv",
            "--skip-existing",
        ],
    )

    assert result.exit_code == 0
    assert exported == {
        "log_dir": "logs",
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
        "csv_path": "results/brief.csv",
        "skip_existing": True,
        "fail_on_log_error": True,
    }
