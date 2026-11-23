import csv
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest
from fsspec.core import url_to_fs
from fsspec.implementations.memory import MemoryFileSystem
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
    InspectScore,
    export_results,
    get_runtime_from_timestamps,
    load_logs,
    prepare_log_results,
    prepare_results,
)


def inspect_log_name(stem: str, suffix: str) -> str:
    """Name a file the way Inspect names logs, so directory discovery accepts it."""
    return f"2026-11-19T12-00-00+00-00_{stem}{suffix}"


@pytest.fixture
def memory_filesystem() -> Iterator[None]:
    """Provide an empty fsspec in-memory filesystem for provider-URI tests."""
    store = MemoryFileSystem.store
    pseudo_dirs = MemoryFileSystem.pseudo_dirs
    MemoryFileSystem.store = {}
    MemoryFileSystem.pseudo_dirs = [""]
    yield
    MemoryFileSystem.store = store
    MemoryFileSystem.pseudo_dirs = pseudo_dirs


def evaluation_log(
    task: str,
    task_id: str,
    accuracy: float,
    run_id: str = "",
) -> EvalLog:
    """Create a deterministic Inspect log containing one accuracy metric."""
    return EvalLog(
        status="success",
        eval=EvalSpec(
            task=task,
            task_id=task_id,
            run_id=run_id,
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
                ),
            ],
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


@pytest.mark.parametrize(
    "run_id",
    [
        pytest.param("run-a", id="plain"),
        pytest.param("=run-1", id="equals"),
        pytest.param("+run-2", id="plus"),
        pytest.param("-run-3", id="minus"),
        pytest.param("@run-4", id="at"),
    ],
)
def test_export_results_skips_existing_run_ids(tmp_path: Path, run_id: str) -> None:
    csv_path = tmp_path / "brief.csv"
    log = evaluation_log("task-a", run_id, 1.0)

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
    discovered = tmp_path / "nested" / inspect_log_name("discovered", ".eval")
    discovered_json = tmp_path / "nested" / inspect_log_name("discovered", ".json")
    discovered.parent.mkdir()
    explicit.touch()
    discovered.touch()
    discovered_json.touch()
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(str(tmp_path), [str(explicit)], False) == [
        str(explicit),
        str(discovered),
        str(discovered_json),
    ]
    assert loaded == [str(explicit), str(discovered), str(discovered_json)]


def test_load_logs_ignores_suffix_matching_directories_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "run.eval"
    log_path.touch()
    (tmp_path / "artifacts.json").mkdir()
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(log_dir=str(tmp_path), fail_on_error=True) == [str(log_path)]
    assert loaded == [str(log_path)]


def test_load_logs_warns_when_no_logs_are_discovered(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / "my_custom_name.json").write_text("{}", encoding="utf-8")

    assert load_logs(log_dir=str(tmp_path), fail_on_error=True) == []

    assert "No Inspect logs found" in caplog.text
    assert "--log-files" in caplog.text


def test_load_logs_ignores_non_log_json_files_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "run.eval"
    log_path.touch()
    for name in ("logs.json", "eval-set.json", "target_metrics.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(log_dir=str(tmp_path), fail_on_error=True) == [str(log_path)]
    assert loaded == [str(log_path)]


def test_load_logs_reads_real_inspect_json_log(tmp_path: Path) -> None:
    log_path = tmp_path / "run.json"
    core.write_eval_log(
        evaluation_log("task-a", "run-a", 0.75),
        log_path,
        format="json",
    )

    loaded_logs = load_logs(log_files=str(log_path))

    assert len(loaded_logs) == 1
    assert loaded_logs[0].eval.task == "task-a"
    assert loaded_logs[0].eval.task_id == "run-a"
    assert loaded_logs[0].results is not None
    assert loaded_logs[0].results.scores[0].metrics["accuracy"].value == pytest.approx(
        0.75,
    )


def test_eval_and_json_inputs_produce_equivalent_csv(tmp_path: Path) -> None:
    log = evaluation_log("task-a", "run-a", 0.75)
    eval_log_path = tmp_path / "run.eval"
    json_log_path = tmp_path / "run.json"
    eval_csv_path = tmp_path / "eval.csv"
    json_csv_path = tmp_path / "json.csv"
    core.write_eval_log(log, eval_log_path)
    core.write_eval_log(log, json_log_path, format="json")

    export_results(
        log_files=str(eval_log_path),
        csv_path=str(eval_csv_path),
        fail_on_log_error=True,
        log_progress=False,
    )
    export_results(
        log_files=str(json_log_path),
        csv_path=str(json_csv_path),
        fail_on_log_error=True,
        log_progress=False,
    )

    assert json_csv_path.read_bytes() == eval_csv_path.read_bytes()


def test_one_run_stored_as_eval_and_json_is_reported_once(tmp_path: Path) -> None:
    log = evaluation_log("task-a", "run-a", 0.75)
    core.write_eval_log(log, tmp_path / inspect_log_name("run", ".eval"))
    core.write_eval_log(
        log,
        tmp_path / inspect_log_name("run", ".json"),
        format="json",
    )

    rows = prepare_results(log_dir=str(tmp_path), log_progress=False)

    assert [(row["Run ID"], row["Metric"]) for row in rows] == [("run-a", "accuracy")]


@pytest.mark.parametrize("input_form", ["log_dir_and_json_file", "json_file_first"])
def test_native_eval_copy_wins_over_a_json_copy_of_the_same_run(
    tmp_path: Path,
    input_form: Literal["log_dir_and_json_file", "json_file_first"],
) -> None:
    """Prefer the `.eval` copy across every source, not only within one scan.

    The two copies carry different scores so the reported row says which one
    survived deduplication.
    """
    native_path = tmp_path / inspect_log_name("run", ".eval")
    json_path = tmp_path / inspect_log_name("run", ".json")
    core.write_eval_log(evaluation_log("task-a", "run-a", 0.25), native_path)
    core.write_eval_log(
        evaluation_log("task-a", "run-a", 0.75),
        json_path,
        format="json",
    )

    if input_form == "log_dir_and_json_file":
        rows = prepare_results(
            log_dir=str(tmp_path),
            log_files=str(json_path),
            log_progress=False,
        )
    else:
        rows = prepare_results(
            log_files=[str(json_path), str(native_path)],
            log_progress=False,
        )

    assert [(row["Run ID"], row["Score"]) for row in rows] == [("run-a", 0.25)]


def test_retried_run_is_reported_separately_from_its_original() -> None:
    """Keep separate attempts that Inspect gives one task ID and two run IDs."""
    rows = prepare_results(
        logs=[
            evaluation_log("task-a", "run-a", 0.25, run_id="invocation-1"),
            evaluation_log("task-a", "run-a", 0.75, run_id="invocation-2"),
        ],
        log_progress=False,
    )

    assert [(row["Run ID"], row["Score"]) for row in rows] == [
        ("run-a", 0.25),
        ("run-a", 0.75),
    ]


def test_log_dir_discovers_eval_logs_whose_names_are_not_inspect_named(
    tmp_path: Path,
) -> None:
    """Discover `.eval` logs by suffix however they are named.

    Inspect's lister parses log file names, and releases before 0.3.221 raise
    on this one, which aborts the whole scan.
    """
    core.write_eval_log(
        evaluation_log("task-a", "run-a", 0.75),
        tmp_path / "run_task_x-1.eval",
    )

    rows = prepare_results(log_dir=str(tmp_path), log_progress=False)

    assert [row["Run ID"] for row in rows] == ["run-a"]


def test_distinct_runs_sharing_a_file_name_are_both_reported(tmp_path: Path) -> None:
    for run_id in ("run-a", "run-b"):
        run_dir = tmp_path / run_id
        run_dir.mkdir()
        core.write_eval_log(
            evaluation_log("task-a", run_id, 0.75),
            run_dir / inspect_log_name("run", ".eval"),
        )

    rows = prepare_results(log_dir=str(tmp_path), log_progress=False)

    assert sorted(row["Run ID"] for row in rows) == ["run-a", "run-b"]


def test_load_logs_deduplicates_equivalent_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "run.eval"
    log_path.touch()
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)
    monkeypatch.chdir(tmp_path)

    assert load_logs(str(tmp_path), "run.eval", False) == [str(log_path)]
    assert loaded == [str(log_path)]


def test_load_logs_deduplicates_file_uris_and_local_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "run.eval"
    log_path.touch()
    single_slash_uri = f"file:{log_path}"
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(
        log_files=[single_slash_uri, log_path.as_uri(), str(log_path)],
    ) == [str(log_path)]
    assert loaded == [str(log_path)]


def test_load_logs_decodes_file_uri_before_validation_and_reading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "run.eval"
    log_path.touch()
    encoded_uri = log_path.as_uri().replace(".eval", ".%65val")
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(log_files=encoded_uri) == [str(log_path)]
    assert loaded == [str(log_path)]


def test_load_logs_preserves_and_deduplicates_remote_uris(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_uri = "s3://inspect-logs/run.eval"
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(log_files=[log_uri, log_uri]) == [log_uri]
    assert loaded == [log_uri]


def test_load_logs_exports_json_beside_remote_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_uri = "s3://inspect-logs/run.eval"
    exported: list[str | Path] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: path)
    monkeypatch.setattr(
        core,
        "write_eval_log",
        lambda log, path, **_: exported.append(path),
    )

    load_logs(log_files=log_uri, export_jsons=True)

    assert exported == ["s3://inspect-logs/run.json"]


@pytest.mark.parametrize(
    "log_file",
    [
        pytest.param("run.json", id="local"),
        pytest.param("s3://inspect-logs/run.json", id="provider-uri"),
    ],
)
def test_load_logs_does_not_overwrite_json_input_when_exporting_sidecars(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    log_file: str,
) -> None:
    if "://" not in log_file:
        log_file = str(tmp_path / log_file)
        Path(log_file).touch()
    monkeypatch.setattr(core, "read_eval_log", lambda path: path)
    monkeypatch.setattr(
        core,
        "write_eval_log",
        lambda *_args, **_kwargs: pytest.fail("JSON input must not be rewritten"),
    )

    assert load_logs(log_files=log_file, export_jsons=True)


def test_load_logs_rejects_log_dir_uri_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reject the URI rather than hand its credentials to Inspect's lister.

    Inspect logs the directory it is listing at DEBUG before touching the
    filesystem, so the real lister runs here and the capture covers its records
    as well as Inspect Brief's own.
    """
    log_dir = "https://alice:password@example.com/logs?token=secret#private"

    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(ValueError, match="does not accept a log directory URI") as error,
    ):
        load_logs(log_dir=log_dir, fail_on_error=True)

    for secret in ("alice", "password", "token", "secret", "example.com"):
        assert secret not in caplog.text
        assert secret not in str(error.value)


def test_load_logs_redacts_uri_credentials_from_diagnostics(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_uri = "https://alice:password@example.com/run.eval?token=secret#private"
    monkeypatch.setattr(
        core,
        "read_eval_log",
        lambda path: (_ for _ in ()).throw(OSError(log_uri)),
    )

    with pytest.raises(ValueError, match="Could not load 1 Inspect log") as error:
        load_logs(log_files=log_uri, fail_on_error=True)

    assert "https://example.com/run.eval" in caplog.text
    assert "https://example.com/run.eval" in str(error.value)
    assert error.value.__cause__ is not None
    for secret in ("alice", "password", "token", "secret", "private"):
        assert secret not in caplog.text
        assert secret not in str(error.value)
        assert secret not in str(error.value.__cause__)


def test_load_logs_redacts_uri_credentials_from_export_messages(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_uri = "https://alice:password@example.com/run.eval?token=secret#private"
    exported: list[str | Path] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: path)
    monkeypatch.setattr(
        core,
        "write_eval_log",
        lambda _log, path, **_kwargs: exported.append(path),
    )

    load_logs(log_files=log_uri, export_jsons=True)

    assert exported == []
    assert "https://example.com/run.eval" in caplog.text
    assert "URI sidecars" in caplog.text
    for secret in ("alice", "password", "token", "secret", "private"):
        assert secret not in caplog.text


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


def test_load_logs_skips_malformed_uris_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    valid = tmp_path / "valid.eval"
    valid.touch()
    loaded: list[str] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)

    assert load_logs(log_files=["https://[broken/run.eval", str(valid)]) == [str(valid)]
    assert loaded == [str(valid)]


def test_load_logs_rejects_unsupported_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unsupported = tmp_path / "run.txt"
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
        == "Inspect Brief supports only .eval and .json log files"
    )


def test_load_logs_handles_directory_scan_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    explicit = tmp_path / "explicit.eval"
    explicit.touch()
    monkeypatch.setattr(
        core,
        "list_eval_logs",
        lambda *_, **__: (_ for _ in ()).throw(OSError("scan unavailable")),
    )
    monkeypatch.setattr(core, "read_eval_log", lambda path: path)

    assert load_logs(log_dir=str(tmp_path), log_files=str(explicit)) == [str(explicit)]
    scan_records = [
        record for record in caplog.records if "Could not scan" in record.message
    ]
    assert len(scan_records) == 1
    assert scan_records[0].exc_info is not None
    assert isinstance(scan_records[0].exc_info[1], OSError)

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
    loaded: list[str] = []
    exported: list[Path] = []
    monkeypatch.setattr(core, "read_eval_log", lambda path: loaded.append(path) or path)
    monkeypatch.setattr(
        core,
        "write_eval_log",
        lambda log, path, **_: exported.append(path),
    )

    load_logs(log_files=str(symlink), export_jsons=True)

    assert loaded == [str(target)]
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

    def record_progress(
        completed_log_count: int,
        total_log_count: int,
        current_task_name: str,
    ) -> None:
        updates.append(
            (completed_log_count, total_log_count, current_task_name),
        )

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

    def progress_callback(
        completed_log_count: int,
        total_log_count: int,
        current_task_name: str,
    ) -> None:
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


def scorer_results_log(status: Literal["success", "error"] = "success") -> EvalLog:
    """Create a log whose two scorer results both report ``accuracy``."""
    log = evaluation_log("task-a", "run-a", 0.0)
    log.status = status
    log.results = EvalResults(
        scores=[
            EvalScore(name="combined", scorer="combined", metrics={}),
            EvalScore(
                name="utility",
                scorer="combined",
                metrics={"accuracy": EvalMetric(name="accuracy", value=0.75)},
            ),
            EvalScore(
                name="security",
                scorer="combined",
                metrics={"accuracy": EvalMetric(name="accuracy", value=0.25)},
            ),
        ],
    )
    return log


def accuracy_target(**options: str) -> InspectScore:
    """Create a normalized percentage accuracy target with optional keys."""
    target = InspectScore(
        name="accuracy",
        is_percentage=True,
        is_higher_better=False,
        is_normalized=True,
    )
    if "score_name" in options:
        target["score_name"] = options["score_name"]
    if "label" in options:
        target["label"] = options["label"]
    return target


def test_target_metrics_select_score_and_label() -> None:
    rows = prepare_log_results(
        scorer_results_log(),
        [
            accuracy_target(),
            accuracy_target(score_name="security", label="attack success rate"),
        ],
        log_progress=False,
    )

    assert [(row["Metric"], row["Score"]) for row in rows] == [
        ("accuracy (%) ⬇️", 75.0),
        ("attack success rate (%) ⬇️", 25.0),
    ]


def test_target_metric_reports_missing_metric_in_named_score() -> None:
    rows = prepare_log_results(
        scorer_results_log(),
        [accuracy_target(score_name="combined", label="combined accuracy")],
        log_progress=False,
    )

    assert [(row["Metric"], row["Score"]) for row in rows] == [
        (
            "combined accuracy (%) ⬇️",
            "Metric 'accuracy' not found in score 'combined'",
        ),
    ]


def test_target_metric_reports_missing_score() -> None:
    rows = prepare_log_results(
        scorer_results_log(),
        [accuracy_target(score_name="secuirty", label="attack success rate")],
        log_progress=False,
    )

    assert [(row["Metric"], row["Score"]) for row in rows] == [
        ("attack success rate (%) ⬇️", "Score 'secuirty' not found"),
    ]


def test_failed_runs_use_target_labels() -> None:
    rows = prepare_log_results(
        scorer_results_log(status="error"),
        [accuracy_target(score_name="security", label="attack success rate")],
        log_progress=False,
    )

    assert [(row["Metric"], row["Score"]) for row in rows] == [
        ("attack success rate (%) ⬇️", "Failed (error)"),
    ]


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


@pytest.mark.parametrize(
    "input_kind",
    [
        "eval-first",
        "json-first",
        "directory",
        "file-uri",
        "native-file-uri",
        "symlink",
        "destination-symlink",
        "hardlink",
    ],
)
def test_load_logs_preserves_json_input_colliding_with_sidecar(
    tmp_path: Path,
    input_kind: str,
) -> None:
    native_path = tmp_path / inspect_log_name("run", ".eval")
    json_path = tmp_path / inspect_log_name("run", ".json")
    core.write_eval_log(evaluation_log("native", "native-run", 0.25), native_path)
    core.write_eval_log(
        evaluation_log("json", "json-run", 0.75),
        json_path,
        format="json",
    )
    original_json = json_path.read_bytes()
    json_source = str(json_path)
    if input_kind == "file-uri":
        json_source = json_path.as_uri()
    elif input_kind in {"symlink", "hardlink"}:
        alias_path = tmp_path / "alias.json"
        if input_kind == "symlink":
            alias_path.symlink_to(json_path)
        else:
            alias_path.hardlink_to(json_path)
        json_source = str(alias_path)
    elif input_kind == "destination-symlink":
        original_path = tmp_path / "original.json"
        json_path.rename(original_path)
        json_path.symlink_to(original_path)
        json_source = str(original_path)
    native_source = (
        native_path.as_uri() if input_kind == "native-file-uri" else str(native_path)
    )
    sources = [native_source, json_source]
    if input_kind == "json-first":
        sources.reverse()
    logs = load_logs(
        log_dir=str(tmp_path) if input_kind == "directory" else None,
        log_files=None if input_kind == "directory" else sources,
        export_jsons=True,
        fail_on_error=True,
    )

    assert json_path.read_bytes() == original_json
    assert sorted(log.eval.task_id for log in logs) == ["json-run", "native-run"]


def test_load_logs_skips_remote_sidecar_colliding_with_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = ["s3://inspect-logs/run.eval", "s3://inspect-logs/run.json"]
    monkeypatch.setattr(core, "read_eval_log", lambda path: path)
    monkeypatch.setattr(
        core,
        "write_eval_log",
        lambda *_args, **_kwargs: pytest.fail("Input must not be overwritten"),
    )

    assert (
        load_logs(log_files=sources, export_jsons=True, fail_on_error=True) == sources
    )


@pytest.mark.parametrize("input_form", ["path", "file_uri", "log_dir"])
def test_load_logs_does_not_write_through_dangling_sidecar_symlink(
    tmp_path: Path,
    input_form: Literal["path", "file_uri", "log_dir"],
) -> None:
    """Refuse the sidecar whichever way the native log reaches the loader.

    Inspect reports discovered logs as ``file:`` URIs, so ``log_dir`` inputs
    reach the export step in URI form just as explicit URIs do.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    native_path = log_dir / inspect_log_name("run", ".eval")
    core.write_eval_log(evaluation_log("native", "native-run", 0.25), native_path)
    outside_target = tmp_path / "clobbered.json"
    sidecar_path = log_dir / inspect_log_name("run", ".json")
    sidecar_path.symlink_to(outside_target)

    if input_form == "log_dir":
        load_logs(log_dir=str(log_dir), export_jsons=True)
    else:
        load_logs(
            log_files=native_path.as_uri()
            if input_form == "file_uri"
            else str(native_path),
            export_jsons=True,
            fail_on_error=True,
        )

    assert not outside_target.exists()


def test_load_logs_preserves_json_input_under_an_equivalent_provider_uri(
    memory_filesystem: None,
) -> None:
    """Compare sidecar destinations with inputs by stored object, not by text.

    ``memory://logs/run.json`` and ``memory:///logs/run.json`` name one object,
    so the native log's sidecar must not be written over the JSON input.
    """
    native_uri = "memory://logs/run.eval"
    json_uri = "memory:///logs/run.json"
    core.write_eval_log(evaluation_log("native", "native-run", 0.25), native_uri)
    core.write_eval_log(
        evaluation_log("sidecar", "json-run", 0.75),
        json_uri,
        format="json",
    )
    filesystem, object_path = url_to_fs(json_uri)
    original_json = filesystem.cat_file(object_path)

    loaded_logs = load_logs(
        log_files=[native_uri, json_uri],
        export_jsons=True,
        fail_on_error=True,
    )

    assert sorted(log.eval.task_id for log in loaded_logs) == [
        "json-run",
        "native-run",
    ]
    assert filesystem.cat_file(object_path) == original_json


def test_load_logs_preserves_unreadable_input_colliding_with_sidecar(
    tmp_path: Path,
) -> None:
    native_path = tmp_path / "run.eval"
    json_path = tmp_path / "run.json"
    core.write_eval_log(evaluation_log("native", "native-run", 0.25), native_path)
    json_path.write_text("not valid JSON", encoding="utf-8")
    original_json = json_path.read_bytes()

    with pytest.raises(ValueError, match="Could not load 1 Inspect log"):
        load_logs(
            log_files=[str(native_path), str(json_path)],
            export_jsons=True,
            fail_on_error=True,
        )

    assert json_path.read_bytes() == original_json
