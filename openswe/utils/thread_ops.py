"""Shared LangGraph thread helpers for the dashboard.

The webhook triggers (Slack / Linear / GitHub) dispatch through
``openswe.dispatch.dispatch_agent_run`` with ``multitask_strategy="interrupt"``,
so they no longer need a busy-check or an in-process lock. The follow-up queue
(``openswe.message_queue``) is retained for the dashboard's deliberate "inject a
follow-up into a run that's already in flight" path.
"""

import logging

from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel

from openswe.config import ENV
from openswe.message_queue import QueuedContent, QueuedMessage

logger = logging.getLogger(__name__)


def langgraph_url() -> str:
    return ENV.LANGGRAPH_URL.get()


def langgraph_client():
    return get_client(url=langgraph_url())


class ThreadRunError(BaseModel):
    """The exception LangGraph records on a thread whose latest run failed."""

    error: str = ""
    message: str = ""

    def describe(self) -> str:
        return ": ".join(part for part in (self.error, self.message) if part)


class _ErroredThread(BaseModel):
    error: ThreadRunError | None = None


async def thread_run_error(thread_id: str, client: LangGraphClient | None = None) -> str | None:
    """Why the thread's latest run failed, or ``None`` when it recorded no error."""
    thread = await (client or langgraph_client()).threads.get(thread_id)
    failure = _ErroredThread.model_validate(thread).error
    return (failure.describe() or None) if failure else None


_RUN_PAGE_SIZE = 100


async def active_run_ids(thread_id: str, client: LangGraphClient | None = None) -> list[str]:
    """Ids of the thread's pending and running runs."""
    client = client or langgraph_client()
    run_ids: set[str] = set()
    for status in ("pending", "running"):
        offset = 0
        while True:
            runs = await client.runs.list(
                thread_id, status=status, limit=_RUN_PAGE_SIZE, offset=offset
            )
            for run in runs:
                run_id = run.get("run_id") or run.get("id")
                if isinstance(run_id, str) and run_id:
                    run_ids.add(run_id)
            if len(runs) < _RUN_PAGE_SIZE:
                break
            offset += len(runs)
    return sorted(run_ids)


async def cancel_active_runs(
    thread_id: str, *, keep_run_id: str = "", client: LangGraphClient | None = None
) -> list[str]:
    """Interrupt the thread's pending and running runs, except ``keep_run_id``.

    Returns the ids it interrupted.
    """
    client = client or langgraph_client()
    run_ids = [
        run_id for run_id in await active_run_ids(thread_id, client) if run_id != keep_run_id
    ]
    if run_ids:
        await client.runs.cancel_many(thread_id=thread_id, run_ids=run_ids, action="interrupt")
    return run_ids


async def get_thread_active_status(thread_id: str) -> bool | None:
    """Return whether the thread is active, or None when status cannot be determined."""
    try:
        thread = await langgraph_client().threads.get(thread_id)
        status = thread.get("status", "idle") if isinstance(thread, dict) else "idle"
        logger.info("Thread %s status check: status=%s", thread_id, status)
        return status == "busy"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to get thread status for %s: %s", thread_id, exc)
        return None


async def queue_message_for_thread(thread_id: str, message_content: QueuedContent) -> bool:
    """Queue a follow-up message for a busy thread's next model call.

    Used by the dashboard to inject a follow-up into a run that's already in
    flight; webhook triggers use ``multitask_strategy="interrupt"`` instead.
    """
    queue_id = message_content.get("queue_id") if isinstance(message_content, dict) else None
    try:
        await QueuedMessage.put(
            thread_id, message_content, queue_id=queue_id if isinstance(queue_id, str) else None
        )
        from openswe.thread_feedback import note_feedback_activity

        await note_feedback_activity(thread_id, client=langgraph_client())
        logger.info("Queued message for thread %s", thread_id)
        return True
    except Exception:
        logger.exception("Failed to queue message for thread %s", thread_id)
        return False
