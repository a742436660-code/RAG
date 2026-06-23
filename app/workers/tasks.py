from app.core.config import get_settings
from app.services.processing import run_document_processing
from app.workers.celery_app import celery_app


@celery_app.task(name="documents.process")
def process_document_task(document_id: str, reindex: bool = False) -> None:
    # Celery worker 实际执行的任务函数，保持很薄，只委托给服务层。
    run_document_processing(document_id, reindex=reindex)


def enqueue_document_processing(document_id: str, reindex: bool = False) -> str:
    # 统一的入队入口：调用方不需要关心当前是 eager 模式还是真 Celery 模式。
    settings = get_settings()
    if settings.celery_task_always_eager:
        # eager 模式下直接执行并返回一个可识别的伪 task_id。
        run_document_processing(document_id, reindex=reindex)
        return f"eager-{document_id}"
    # 非 eager 模式下提交给 Redis/Celery，由 worker 异步处理。
    result = process_document_task.delay(document_id, reindex)
    return str(result.id)
