"""Celery application for background investigation runs (§11).

Run a worker locally (Windows needs the solo pool):
    celery -A app.worker.celery_app worker --pool=solo --loglevel=INFO
"""

from celery import Celery

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()

INVESTIGATION_TASK_NAME = "investigations.run"
INVESTIGATION_QUEUE = "investigations"

celery_app = Celery(
    "incident_platform",
    broker=settings.REDIS_URL,
    include=["app.worker.tasks.investigation"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,  # run state lives in investigation_runs, not the result backend
    task_default_queue=INVESTIGATION_QUEUE,
    # Ack only after the task finishes and redeliver if the worker dies mid-run;
    # the task is idempotent and resumes from the LangGraph checkpoint (§7).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=settings.INVESTIGATION_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=settings.INVESTIGATION_TIME_LIMIT_SECONDS,
    # Redis redelivers unacked tasks after this window — must exceed the hard time limit
    broker_transport_options={"visibility_timeout": settings.INVESTIGATION_TIME_LIMIT_SECONDS + 300},
    broker_connection_timeout=3,
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,  # keep the structlog JSON handler installed above
    timezone="UTC",
    enable_utc=True,
)
