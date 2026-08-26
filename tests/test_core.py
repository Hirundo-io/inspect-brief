from pathlib import Path

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

from inspect_brief.core import export_results


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
