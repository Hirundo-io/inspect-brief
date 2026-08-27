import csv
from pathlib import Path

import pytest
from inspect_ai.log import (
    EvalConfig,
    EvalDataset,
    EvalLog,
    EvalMetric,
    EvalResults,
    EvalScore,
    EvalSpec,
    EvalStats,
)

from inspect_brief import core
from inspect_brief.core import export_results, load_logs, prepare_log_results


def evaluation_log(task: str, task_id: str, accuracy: float) -> EvalLog:
    """Create a deterministic Inspect log containing one accuracy metric."""
    return EvalLog(
        status="success",
        eval=EvalSpec(
            task=task,
            task_id=task_id,
            created="2026-08-26T12:00:00+00:00",
            dataset=EvalDataset(),
            model="test",
            config=EvalConfig(),
        ),
        results=EvalResults(
            scores=[
                EvalScore(
                    name="choice",
                    scorer="choice",
                    metrics={
                        "accuracy": EvalMetric(name="accuracy", value=accuracy),
                    },
                )
            ]
        ),
        stats=EvalStats(
            started_at="2026-08-26T12:00:00+00:00",
            completed_at="2026-08-26T12:00:02+00:00",
        ),
    )


def test_export_results_filters_tasks(tmp_path: Path) -> None:
    csv_path = tmp_path / "brief.csv"

    row_count = export_results(
        logs=[
            evaluation_log("task-a", "run-a", 1.0),
            evaluation_log("task-b", "run-b", 0.5),
            evaluation_log("task-c", "run-c", 0.0),
        ],
        tasks=["task-a", "task-b"],
        csv_path=str(csv_path),
        log_progress=False,
    )

    assert row_count == 2
    rows = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3
    assert ",task-a," in rows[1]
    assert ",task-b," in rows[2]
    assert all("task-c" not in row for row in rows)


def test_export_results_skips_existing_run_ids(tmp_path: Path) -> None:
    csv_path = tmp_path / "brief.csv"
    log = evaluation_log("task-a", "run-a", 1.0)

    assert export_results(logs=[log], csv_path=str(csv_path), log_progress=False) == 1
    assert (
        export_results(
            logs=[log],
            csv_path=str(csv_path),
            skip_existing=True,
            log_progress=False,
        )
        == 0
    )

    assert len(csv_path.read_text(encoding="utf-8").splitlines()) == 2


def test_load_logs_combines_explicit_files_and_directory(
    monkeypatch, tmp_path: Path
) -> None:
    explicit = tmp_path / "explicit.eval"
    discovered = tmp_path / "nested" / "discovered.eval"
    discovered.parent.mkdir()
    explicit.touch()
    discovered.touch()
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(str(tmp_path), [str(explicit)], False) == [
        str(explicit),
        str(discovered),
    ]
    assert loaded == [str(explicit), str(discovered)]


def test_load_logs_deduplicates_equivalent_paths(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "run.eval"
    log_path.touch()
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)
    monkeypatch.chdir(tmp_path)

    assert load_logs(str(tmp_path), "run.eval", False) == ["run.eval"]
    assert loaded == ["run.eval"]


def test_load_logs_skips_unresolvable_paths_and_continues(
    monkeypatch, tmp_path: Path
) -> None:
    loop = tmp_path / "loop.eval"
    loop.symlink_to(loop)
    valid = tmp_path / "valid.eval"
    valid.touch()
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(log_files=[str(loop), str(valid)]) == [str(valid)]
    assert loaded == [str(valid)]


def test_load_logs_exports_json_beside_symlink(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "target.eval"
    target.touch()
    symlink = tmp_path / "linked.eval"
    symlink.symlink_to(target)
    exported: list[Path] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: path)
    monkeypatch.setattr(
        core,
        "write_eval_log",
        lambda log, path, **_: exported.append(path),
    )

    load_logs(log_files=str(symlink), export_jsons=True)

    assert exported == [symlink.with_suffix(".json")]


def test_empty_target_metric_selection_exports_no_rows() -> None:
    assert prepare_log_results(evaluation_log("task-a", "run-a", 1.0), []) == []


def test_export_escapes_formula_cells_and_migrates_header_only_csv(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "brief.csv"
    csv_path.write_text("Legacy\n", encoding="utf-8")
    log = evaluation_log("=task-a", "run-a", 1.0)

    export_results(logs=[log], csv_path=str(csv_path), log_progress=False)

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["Benchmark"] == "'=task-a"


def test_skip_existing_rejects_legacy_rows_without_run_id(tmp_path: Path) -> None:
    csv_path = tmp_path / "brief.csv"
    csv_path.write_text("Legacy\nvalue\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no 'Run ID' column"):
        export_results(
            logs=[evaluation_log("task-a", "run-a", 1.0)],
            csv_path=str(csv_path),
            skip_existing=True,
            log_progress=False,
        )


def test_export_rejects_existing_rows_with_extra_cells(tmp_path: Path) -> None:
    csv_path = tmp_path / "brief.csv"
    csv_path.write_text("Created,Run ID\ncreated,run-a,unexpected\n", encoding="utf-8")

    with pytest.raises(Exception, match="more values than its header"):
        export_results(
            logs=[evaluation_log("task-a", "run-a", 1.0)],
            csv_path=str(csv_path),
            log_progress=False,
        )
