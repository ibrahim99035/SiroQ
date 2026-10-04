"""Background analysis worker.

Runs as its own process (on Render: a background worker service, not the web
service) and does nothing but drain the analyses queue written by
app/analytics_service/queue.py.

Deliberately a bare polling loop rather than Celery or an event consumer: the
queue is one Postgres table, there is one worker, and the alternative would add a
second datastore whose only job is to move rows between two processes.

    python -m app.worker
"""
import logging
import os
import signal
import sys
import time

from app.analytics_service import queue
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger("siroq.worker")

# Set by the SIGTERM handler so the loop finishes the job it is holding instead
# of dropping it mid-write and leaving it in "running" until it looks abandoned.
_stopping = False


def _install_signal_handlers() -> None:
    def _request_stop(signum, _frame):
        global _stopping
        _stopping = True
        logger.info("received %s; finishing current job then exiting", signal.Signals(signum).name)

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)


def _recover(session) -> None:
    """Put anything left "running" by a previous process back on the queue."""
    try:
        queue.requeue_abandoned(session, settings.WORKER_STALE_AFTER_SECONDS)
    except Exception:  # noqa: BLE001 - never refuse to boot over a recovery hiccup
        logger.exception("startup recovery failed; continuing")


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _install_signal_handlers()

    logger.info(
        "worker starting (poll=%.1fs stale_after=%ds storage=%s)",
        settings.WORKER_POLL_INTERVAL_SECONDS,
        settings.WORKER_STALE_AFTER_SECONDS,
        settings.STORAGE_DRIVER,
    )

    # The loop owns one session for its lifetime. Each unit of work commits, so
    # nothing is left open between iterations — unlike a session per request, this
    # avoids reconnecting (and re-authenticating to Neon) on every poll.
    with SessionLocal() as session:
        _recover(session)

        while not _stopping:
            try:
                analysis_id = queue.claim_next(session)
            except Exception:  # noqa: BLE001 - a blip must not end the worker
                logger.exception("claim failed; backing off")
                time.sleep(min(settings.WORKER_POLL_INTERVAL_SECONDS * 5, 30))
                continue

            if analysis_id is None:
                if _stopping:
                    break
                time.sleep(settings.WORKER_POLL_INTERVAL_SECONDS)
                continue

            logger.info("claimed analysis %s", analysis_id)
            try:
                queue.run_claimed(session, analysis_id)
            except Exception:  # noqa: BLE001 - run_claimed is terminal, but be sure
                logger.exception("analysis %s raised out of the worker", analysis_id)
                session.rollback()
                try:
                    queue.fail(session, analysis_id, "Worker crashed while running this job")
                except Exception:  # noqa: BLE001
                    logger.exception("could not even record the failure for %s", analysis_id)

    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    # Import check: fail loudly rather than exiting 0 if the DB is unreachable.
    if not os.environ.get("DATABASE_URL") and not os.path.exists(".env"):
        logger.warning("no DATABASE_URL or .env found; relying on defaults")
    sys.exit(main())
