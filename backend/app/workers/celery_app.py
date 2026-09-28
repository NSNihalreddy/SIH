from celery import Celery

from app.core.config import REDIS_URL

celery_app = Celery("data_mine", broker=REDIS_URL, backend=REDIS_URL, include=["app.workers.tasks"])
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    enable_utc=True,
    timezone="UTC",
    task_track_started=True,
    task_publish_retry=False,
    broker_connection_timeout=3,
    broker_connection_retry_on_startup=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=3600,
    worker_hijack_root_logger=False,
    task_routes={"app.workers.tasks.generate_report_task": {"queue": "reports"}},
)
