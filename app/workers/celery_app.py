"""
Celery-приложение SyncScribe.

Требования к воркер (H-6)
---------------------------------
Задачи в analysis_tasks.py используют asyncio.run() для запуска async-кода из
синхронного Celery-таска. asyncio.run() создаёт новый event loop на каждый
вызов и закрывает его по завершении. Это безопасно только при использовании
пула процессов (prefork, по умолчанию).

НЕЛЬЗЯ запускать воркер с пулами:
  - gevent  (celery worker -P gevent)
  - eventlet (celery worker -P eventlet)
  - solo     (однопоточный, только для отладки в dev)

При этих пулах asyncio.run() вызывает RuntimeError("уже запущен event loop")
или создаёт вложенный цикл. Допустимая команда запуска воркера:

    celery -A app.workers.celery_app worker --loglevel=info
    # или явно:
    celery -A app.workers.celery_app worker --loglevel=info -P prefork

Если в будущем потребуется перейти на gevent/eventlet —
замените asyncio.run() на loop.run_until_complete() с получением
текущего loop через asyncio.get_event_loop().
"""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

# P0-2b: lease захвата источника (analysis_tasks._SOURCE_LEASE_SECONDS = 600) должен быть
# больше жёсткого лимита задачи, иначе источник могут перехватить у живого воркера.
_TASK_SOFT_TIME_LIMIT_SECONDS = 540
_TASK_TIME_LIMIT_SECONDS = 570

celery_app = Celery(
    "syncscribe",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks.analysis_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    # H-4: при падении воркера незавершённая задача возвращается в очередь,
    # а не теряется. Работает в паре с task_acks_late=True.
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
    task_soft_time_limit=_TASK_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=_TASK_TIME_LIMIT_SECONDS,
)
