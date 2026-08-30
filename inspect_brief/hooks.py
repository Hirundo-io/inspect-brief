"""Inspect lifecycle hook for exporting evaluation briefs."""

import logging
import os

from dotenv import load_dotenv
from inspect_ai.hooks import Hooks, RunEnd, TaskEnd, hooks

from inspect_brief.core import export_results
from inspect_brief.parsing import parse_comma_separated, parse_target_metrics

_CSV_PATH_ENV = "INSPECT_BRIEF_CSV_PATH"
_ENABLED_ENV = "INSPECT_BRIEF_ENABLED"
_TASKS_ENV = "INSPECT_BRIEF_TASKS"
_TARGET_METRICS_ENV = "INSPECT_BRIEF_TARGET_METRICS"
_SKIP_EXISTING_ENV = "INSPECT_BRIEF_SKIP_EXISTING"

load_dotenv()
logger = logging.getLogger(__name__)


def environment_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable using Inspect Brief conventions.

    Args:
        name: The environment variable name.
        default: The value to return when the variable is unset.

    Returns:
        True for ``1``, ``true``, ``yes``, or ``on``; otherwise False or the
        supplied default.
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@hooks(name="inspect_brief", description="Export Inspect evaluation metrics to CSV")
class InspectBriefHooks(Hooks):
    """Append configured metric summaries when an Inspect task completes."""

    def __init__(self) -> None:
        self._run_summaries: dict[str, tuple[int, int, int, str]] = {}

    def enabled(self) -> bool:
        """Return whether the hook has enough configuration to run.

        Returns:
            True when a CSV path is configured and the hook is not disabled.
        """
        return environment_flag(
            _ENABLED_ENV,
            default=bool(os.getenv(_CSV_PATH_ENV)),
        ) and bool(os.getenv(_CSV_PATH_ENV))

    async def on_task_end(self, data: TaskEnd) -> None:
        """Export the completed task log using hook environment configuration.

        Args:
            data: The completed Inspect task event.
        """
        csv_path = os.environ[_CSV_PATH_ENV]
        try:
            target_metrics = parse_target_metrics(
                os.getenv(_TARGET_METRICS_ENV),
                source=_TARGET_METRICS_ENV,
            )
        except ValueError as error:
            logger.error(
                "[inspect-brief] invalid configuration task=%s: %s",
                data.log.eval.task,
                error,
            )
            return

        try:
            row_count = export_results(
                logs=data.log,
                tasks=parse_comma_separated(os.getenv(_TASKS_ENV)),
                target_metrics=target_metrics,
                csv_path=csv_path,
                skip_existing=environment_flag(_SKIP_EXISTING_ENV),
                log_progress=False,
            )
        except Exception:
            logger.exception(
                "[inspect-brief] CSV export failed task=%s csv=%s",
                data.log.eval.task,
                csv_path,
            )
            return

        task_count, failed_task_count, result_count, _ = self._run_summaries.get(
            data.run_id, (0, 0, 0, csv_path)
        )
        self._run_summaries[data.run_id] = (
            task_count + 1,
            failed_task_count + (data.log.status != "success"),
            result_count + row_count,
            csv_path,
        )
        logger.info(
            "[inspect-brief] exported rows=%s task=%s",
            row_count,
            data.log.eval.task,
        )

    async def on_run_end(self, data: RunEnd) -> None:
        """Log the brief-export summary for a completed Inspect run.

        Args:
            data: The completed Inspect run event.
        """
        task_count, failed_task_count, row_count, csv_path = self._run_summaries.pop(
            data.run_id, (0, 0, 0, "N/A")
        )
        if task_count:
            logger.info(
                "[inspect-brief] summary tasks=%s failed=%s rows=%s csv: %s",
                task_count,
                failed_task_count,
                row_count,
                csv_path,
            )
