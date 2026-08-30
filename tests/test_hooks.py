import asyncio
import logging
from typing import Literal

import pytest
from inspect_ai._eval.eval import EvalLogs
from inspect_ai.hooks import RunEnd, TaskEnd
from inspect_ai.log import EvalConfig, EvalDataset, EvalLog, EvalSpec

from inspect_brief import hooks


def clear_hook_environment(monkeypatch) -> None:
    """Clear environment variables that configure the Inspect Brief hook."""
    for name in (
        "INSPECT_BRIEF_CSV_PATH",
        "INSPECT_BRIEF_ENABLED",
        "INSPECT_BRIEF_TASKS",
        "INSPECT_BRIEF_TARGET_METRICS",
        "INSPECT_BRIEF_SKIP_EXISTING",
    ):
        monkeypatch.delenv(name, raising=False)


def task_end(
    task: str,
    status: Literal["started", "success", "cancelled", "error"] = "success",
) -> TaskEnd:
    """Create a representative completed Inspect task event."""
    return TaskEnd(
        eval_set_id=None,
        run_id="run-1",
        eval_id="eval-1",
        log=EvalLog(
            status=status,
            eval=EvalSpec(
                task=task,
                task_id="task-1",
                created="2026-08-26T12:00:00+00:00",
                dataset=EvalDataset(),
                model="test",
                config=EvalConfig(),
            ),
        ),
    )


def run_end(exception: BaseException | None = None) -> RunEnd:
    """Create a representative completed Inspect run event."""
    return RunEnd(
        eval_set_id=None,
        run_id="run-1",
        exception=exception,
        logs=EvalLogs(),
    )


def test_hook_is_disabled_without_csv_path(monkeypatch) -> None:
    clear_hook_environment(monkeypatch)

    assert not hooks.InspectBriefHooks().enabled()


def test_hook_is_disabled_when_explicitly_disabled(monkeypatch) -> None:
    clear_hook_environment(monkeypatch)
    monkeypatch.setenv("INSPECT_BRIEF_CSV_PATH", "results/brief.csv")
    monkeypatch.setenv("INSPECT_BRIEF_ENABLED", "false")

    assert not hooks.InspectBriefHooks().enabled()


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_hook_is_enabled_by_supported_true_values(monkeypatch, value: str) -> None:
    clear_hook_environment(monkeypatch)
    monkeypatch.setenv("INSPECT_BRIEF_CSV_PATH", "results/brief.csv")
    monkeypatch.setenv("INSPECT_BRIEF_ENABLED", value)

    assert hooks.InspectBriefHooks().enabled()


def test_hook_requires_csv_path_when_explicitly_enabled(monkeypatch) -> None:
    clear_hook_environment(monkeypatch)
    monkeypatch.setenv("INSPECT_BRIEF_ENABLED", "true")

    assert not hooks.InspectBriefHooks().enabled()


def test_hook_exports_task_and_logs_run_summary(monkeypatch, caplog) -> None:
    clear_hook_environment(monkeypatch)
    caplog.set_level(logging.INFO, logger=hooks.__name__)
    monkeypatch.setenv("INSPECT_BRIEF_CSV_PATH", "results/brief.csv")
    exported: dict[str, object] = {}

    def export_results(**kwargs) -> int:
        exported.update(kwargs)
        return 2

    monkeypatch.setattr(hooks, "export_results", export_results)
    hook = hooks.InspectBriefHooks()
    task = task_end("inspect_evals/hellaswag")

    asyncio.run(hook.on_task_end(task))
    asyncio.run(hook.on_run_end(run_end()))

    assert exported["logs"] is task.log
    assert exported["csv_path"] == "results/brief.csv"
    assert exported["log_progress"] is False
    assert "[inspect-brief] exported rows=2 task=inspect_evals/hellaswag" in caplog.text
    assert (
        "[inspect-brief] summary tasks=1 failed=0 rows=2 csv: results/brief.csv"
        in caplog.text
    )


def test_hook_includes_failed_tasks_in_run_summary(monkeypatch, caplog) -> None:
    clear_hook_environment(monkeypatch)
    caplog.set_level(logging.INFO, logger=hooks.__name__)
    monkeypatch.setenv("INSPECT_BRIEF_CSV_PATH", "results/brief.csv")
    monkeypatch.setattr(hooks, "export_results", lambda **kwargs: 1)
    hook = hooks.InspectBriefHooks()

    asyncio.run(hook.on_task_end(task_end("successful-task")))
    asyncio.run(hook.on_task_end(task_end("failed-task", status="error")))
    asyncio.run(hook.on_run_end(run_end()))

    assert (
        "[inspect-brief] summary tasks=2 failed=1 rows=2 csv: results/brief.csv"
        in caplog.text
    )


def test_hook_parses_environment_configuration(monkeypatch) -> None:
    clear_hook_environment(monkeypatch)
    monkeypatch.setenv("INSPECT_BRIEF_CSV_PATH", "results/brief.csv")
    monkeypatch.setenv("INSPECT_BRIEF_TASKS", "task-a, task-b")
    monkeypatch.setenv("INSPECT_BRIEF_SKIP_EXISTING", "yes")
    monkeypatch.setenv(
        "INSPECT_BRIEF_TARGET_METRICS",
        '{"task-a":[{"name":"accuracy","is_percentage":false,'
        '"is_higher_better":true,"is_normalized":false}]}',
    )
    exported: dict[str, object] = {}

    def export_results(**kwargs) -> int:
        exported.update(kwargs)
        return 1

    monkeypatch.setattr(hooks, "export_results", export_results)
    task = task_end("task-a")

    asyncio.run(hooks.InspectBriefHooks().on_task_end(task))

    assert exported["tasks"] == ["task-a", "task-b"]
    assert exported["skip_existing"] is True
    assert exported["target_metrics"] == {
        "task-a": [
            {
                "name": "accuracy",
                "is_percentage": False,
                "is_higher_better": True,
                "is_normalized": False,
            }
        ]
    }


def test_hook_logs_export_errors(monkeypatch, caplog) -> None:
    clear_hook_environment(monkeypatch)
    monkeypatch.setenv("INSPECT_BRIEF_CSV_PATH", "results/brief.csv")

    def export_results(**kwargs) -> int:
        raise RuntimeError("cannot write CSV")

    monkeypatch.setattr(hooks, "export_results", export_results)
    task = task_end("inspect_evals/hellaswag")

    hook = hooks.InspectBriefHooks()
    asyncio.run(hook.on_task_end(task))
    asyncio.run(hook.on_run_end(run_end()))

    assert (
        "[inspect-brief] CSV export failed task=inspect_evals/hellaswag "
        "csv=results/brief.csv" in caplog.text
    )
    assert (
        "[inspect-brief] summary tasks=1 failed=0 export_errors=1 rows=0 "
        "csv: results/brief.csv" in caplog.text
    )


def test_hook_logs_invalid_metric_configuration(monkeypatch, caplog) -> None:
    clear_hook_environment(monkeypatch)
    monkeypatch.setenv("INSPECT_BRIEF_CSV_PATH", "results/brief.csv")
    monkeypatch.setenv("INSPECT_BRIEF_TARGET_METRICS", "not-json")
    exporter_called = False

    def export_results(**kwargs) -> int:
        nonlocal exporter_called
        exporter_called = True
        return 0

    monkeypatch.setattr(hooks, "export_results", export_results)
    task = task_end("inspect_evals/hellaswag")

    hook = hooks.InspectBriefHooks()
    asyncio.run(hook.on_task_end(task))
    asyncio.run(hook.on_run_end(run_end()))

    assert not exporter_called
    assert (
        "[inspect-brief] invalid configuration task=inspect_evals/hellaswag"
        in caplog.text
    )
    assert (
        "INSPECT_BRIEF_TARGET_METRICS inline value 'not-json' is not valid JSON"
        in caplog.text
    )
    assert (
        "[inspect-brief] summary tasks=1 failed=0 export_errors=1 rows=0 "
        "csv: results/brief.csv" in caplog.text
    )


def test_hook_logs_run_exception_without_completed_tasks(caplog) -> None:
    caplog.set_level(logging.INFO, logger=hooks.__name__)
    hook = hooks.InspectBriefHooks()

    asyncio.run(hook.on_run_end(run_end(RuntimeError("run setup failed"))))

    assert (
        "[inspect-brief] summary tasks=0 failed=0 export_errors=0 rows=0 "
        "csv: N/A run_exception=RuntimeError('run setup failed')" in caplog.text
    )


def test_hook_logs_run_exception_with_partial_summary(monkeypatch, caplog) -> None:
    clear_hook_environment(monkeypatch)
    caplog.set_level(logging.INFO, logger=hooks.__name__)
    monkeypatch.setenv("INSPECT_BRIEF_CSV_PATH", "results/brief.csv")
    monkeypatch.setattr(hooks, "export_results", lambda **kwargs: 2)
    hook = hooks.InspectBriefHooks()

    asyncio.run(hook.on_task_end(task_end("successful-task")))
    asyncio.run(hook.on_run_end(run_end(RuntimeError("run interrupted"))))

    assert (
        "[inspect-brief] summary tasks=1 failed=0 export_errors=0 rows=2 "
        "csv: results/brief.csv run_exception=RuntimeError('run interrupted')"
        in caplog.text
    )
