import asyncio
import datetime as dt
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..config import get_settings
from ..database import get_db
from ..dependencies import broker
from ..models import TaskRecord, User
from ..schemas import UploadResponse
from ..services.pdf_utils import try_extract_title
from ..storage import domain_tag_from_template
from ..worker import execute_pipeline

logger = logging.getLogger(__name__)
router = APIRouter(tags=["upload"])
settings = get_settings()


@router.post("/upload", response_model=UploadResponse)
async def upload_paper(
    file: UploadFile = File(...),
    target_language: str = Form(default="Chinese"),
    summary_template: str = Form(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持上传 PDF 文件。")

    from ..dependencies import storage

    if not summary_template:
        summary_template = storage.default_template

    file_content = await file.read()
    title_candidate = try_extract_title(file_content, file.filename)

    paper_id = storage.allocate_paper_id(title_candidate)
    task_id = uuid.uuid4().hex
    template_domain = domain_tag_from_template(summary_template)

    await file.seek(0)
    storage.save_upload(paper_id, file, source_filename=file.filename)
    await file.close()

    output_dir = str(storage.paper_output_dir(paper_id))
    pdf_path = str(storage.pdf_path(paper_id))

    from ..models import Paper

    paper = Paper(
        paper_id=paper_id,
        title=title_candidate,
        source_filename=file.filename,
        status="processing",
        target_language=target_language,
        summary_template=summary_template,
        year=dt.datetime.now(dt.timezone.utc).year,
        authors=[],
        domain_tags=[template_domain],
        pdf_path=pdf_path,
        output_dir=output_dir,
        user_id=user.id,
    )
    db.add(paper)

    task_record = TaskRecord(
        task_id=task_id,
        paper_id=paper_id,
        status="queued",
        progress=0,
        message="任务已创建，正在排队",
    )
    db.add(task_record)
    await db.flush()

    await broker.create(
        task_id,
        paper_id,
        generation_model_name=settings.generation_model_name,
        evaluation_model_name=settings.evaluation_model_name,
        collaboration_mode=(
            f"Multi-Agent Collaboration: "
            f"{settings.generation_model_name} (Gen) + {settings.evaluation_model_name} (Eval)"
        ),
    )
    logger.info(
        "Upload accepted: task_id=%s paper_id=%s file=%s template=%s user_id=%d",
        task_id, paper_id, file.filename, summary_template, user.id,
    )
    asyncio.create_task(
        execute_pipeline(
            task_id=task_id,
            paper_id=paper_id,
            title=title_candidate,
            target_language=target_language,
            template_name=summary_template,
            user_id=user.id,
        )
    )
    return UploadResponse(task_id=task_id, paper_id=paper_id)
