from celery import Celery

from app.core.config import get_settings

settings = get_settings()

# Celery 应用只负责后台任务调度；真正的业务处理在 services/processing.py。
celery_app = Celery(
    "local_enterprise_rag",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    # task_always_eager=True 时任务在调用方进程同步执行，适合本地开发和测试。
    # false 时任务会经过 Redis broker，由 worker 进程异步消费。
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
)
