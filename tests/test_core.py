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
from inspect_brief.core import (
    export_results,
    get_runtime_from_timestamps,
    load_logs,
    prepare_log_results,
    prepare_results,
)


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


def test_runtime_accepts_utc_z_suffix() -> None:
    assert (
        get_runtime_from_timestamps("2026-08-26T12:00:00Z", "2026-08-26T12:00:02Z") == 2
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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


def test_load_logs_deduplicates_equivalent_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "run.eval"
    log_path.touch()
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)
    monkeypatch.chdir(tmp_path)

    assert load_logs(str(tmp_path), "run.eval", False) == ["run.eval"]
    assert loaded == ["run.eval"]


def test_load_logs_skips_unresolvable_paths_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    loop = tmp_path / "loop.eval"
    loop.symlink_to(loop)
    valid = tmp_path / "valid.eval"
    valid.touch()
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(log_files=[str(loop), str(valid)]) == [str(valid)]
    assert loaded == [str(valid)]


def test_load_logs_rejects_non_eval_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unsupported = tmp_path / "run.json"
    supported = tmp_path / "run.eval"
    unsupported.touch()
    supported.touch()
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(log_files=[str(unsupported), str(supported)]) == [str(supported)]
    assert loaded == [str(supported)]

    with pytest.raises(ValueError, match="Could not load 1 Inspect log") as error:
        load_logs(log_files=str(unsupported), fail_on_error=True)

    assert isinstance(error.value.__cause__, ValueError)
    assert (
        str(error.value.__cause__)
        == "Inspect Brief currently supports only .eval log files"
    )


def test_load_logs_handles_directory_scan_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit.eval"
    explicit.touch()
    monkeypatch.setattr(
        core.Path,
        "rglob",
        lambda *_: (_ for _ in ()).throw(OSError("scan unavailable")),
    )
    monkeypatch.setattr(core, "read_eval_log", lambda path: path)

    assert load_logs(log_dir=str(tmp_path), log_files=str(explicit)) == [str(explicit)]

    with pytest.raises(ValueError, match="directory scan") as error:
        load_logs(
            log_dir=str(tmp_path),
            log_files=str(explicit),
            fail_on_error=True,
        )

    assert isinstance(error.value.__cause__, OSError)


def test_load_logs_exports_json_beside_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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


def test_export_results_rejects_load_errors_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "unreadable.eval"
    csv_path = tmp_path / "brief.csv"
    monkeypatch.setattr(
        core,
        "read_eval_log",
        lambda path: (_ for _ in ()).throw(OSError("invalid log")),
    )

    with pytest.raises(ValueError, match="Could not load 1 Inspect log"):
        export_results(
            log_files=str(log_path),
            csv_path=str(csv_path),
            fail_on_log_error=True,
            log_progress=False,
        )

    assert not csv_path.exists()


def test_prepare_results_preserves_positional_log_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_progress_values: list[bool] = []
    monkeypatch.setattr(
        core,
        "prepare_log_results",
        lambda _, __, log_progress: log_progress_values.append(log_progress) or [],
    )

    prepare_results(
        None,
        None,
        [evaluation_log("task-a", "run-a", 1.0)],
        None,
        None,
        None,
        False,
        False,
    )

    assert log_progress_values == [False]


def test_prepare_results_reports_progress_without_per_task_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_progress_values: list[bool] = []
    updates: list[tuple[int, int, str]] = []
    logs = [
        evaluation_log("task-a", "run-a", 1.0),
        evaluation_log("task-b", "run-b", 0.5),
    ]
    monkeypatch.setattr(
        core,
        "prepare_log_results",
        lambda _, __, log_progress: log_progress_values.append(log_progress) or [],
    )

    def record_progress(completed: int, total: int, task: str) -> None:
        updates.append((completed, total, task))

    prepare_results(logs=logs, progress_callback=record_progress)

    assert log_progress_values == [False, False]
    assert updates == [(1, 2, "task-a"), (2, 2, "task-b")]


def test_export_results_preserves_positional_log_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        core,
        "prepare_results",
        lambda **kwargs: captured.update(kwargs) or [],
    )

    export_results(
        None,
        None,
        [evaluation_log("task-a", "run-a", 1.0)],
        None,
        None,
        str(tmp_path / "brief.csv"),
        False,
        False,
        False,
    )

    assert captured["log_progress"] is False
    assert captured["fail_on_log_error"] is False


def test_export_results_passes_progress_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def progress_callback(completed: int, total: int, task: str) -> None:
        pass

    monkeypatch.setattr(
        core,
        "prepare_results",
        lambda **kwargs: captured.update(kwargs) or [],
    )

    export_results(
        logs=[evaluation_log("task-a", "run-a", 1.0)],
        csv_path=str(tmp_path / "brief.csv"),
        log_progress=False,
        progress_callback=progress_callback,
    )

    assert captured["progress_callback"] is progress_callback


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


def test_skip_existing_rejects_rows_with_missing_run_id(tmp_path: Path) -> None:
    csv_path = tmp_path / "brief.csv"
    csv_path.write_text("Created,Run ID\ncreated\n", encoding="utf-8")

    with pytest.raises(ValueError, match="row has no Run ID"):
        export_results(
            logs=[evaluation_log("task-a", "run-a", 1.0)],
            csv_path=str(csv_path),
            skip_existing=True,
            log_progress=False,
        )


def test_export_rejects_existing_rows_with_extra_cells(tmp_path: Path) -> None:
    csv_path = tmp_path / "brief.csv"
    csv_path.write_text("Created,Run ID\ncreated,run-a,unexpected\n", encoding="utf-8")

    with pytest.raises(ValueError, match="more values than its header"):
        export_results(
            logs=[evaluation_log("task-a", "run-a", 1.0)],
            csv_path=str(csv_path),
            log_progress=False,
        )


def test_header_migration_preserves_original_when_rewrite_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "brief.csv"
    original = "Legacy\nvalue\n"
    csv_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        core.os,
        "replace",
        lambda *_: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(RuntimeError, match="replace failed"):
        export_results(
            logs=[evaluation_log("task-a", "run-a", 1.0)],
            csv_path=str(csv_path),
            log_progress=False,
        )

    assert csv_path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".brief.csv.*.tmp")) == []
