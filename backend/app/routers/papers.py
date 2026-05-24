from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..database import get_db
from ..dependencies import storage
from ..models import Paper, User
from ..schemas import ChatRequest, ChatResponse, ContentResponse, PaperMeta
from ..services.chat import chat_with_paper
from ..storage import domain_tag_from_template, unique_keep_order

router = APIRouter(tags=["papers"])


def _paper_to_meta(paper: Paper) -> PaperMeta:
    template_tag = domain_tag_from_template(paper.summary_template)
    domain_tags = unique_keep_order([template_tag, *(paper.domain_tags or [])])
    return PaperMeta(
        paper_id=paper.paper_id,
        title=paper.title,
        source_filename=paper.source_filename or "",
        created_at=paper.created_at.isoformat(),
        target_language=paper.target_language,
        summary_template=paper.summary_template,
        status=paper.status,
        year=paper.year,
        authors=paper.authors or [],
        domain_tags=domain_tags,
    )


async def _get_user_paper(
    paper_id: str, user: User, db: AsyncSession
) -> Paper:
    result = await db.execute(select(Paper).where(Paper.paper_id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    if paper.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="无权访问此论文。")
    return paper


@router.get("/papers", response_model=list[PaperMeta])
async def list_papers(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role == "admin":
        result = await db.execute(
            select(Paper).order_by(Paper.created_at.desc())
        )
    else:
        result = await db.execute(
            select(Paper)
            .where(Paper.user_id == user.id)
            .order_by(Paper.created_at.desc())
        )
    return [_paper_to_meta(p) for p in result.scalars().all()]


@router.get("/papers/{paper_id}", response_model=PaperMeta)
async def get_paper(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    paper = await _get_user_paper(paper_id, user, db)
    return _paper_to_meta(paper)


@router.get("/papers/{paper_id}/content/{kind}", response_model=ContentResponse)
async def get_content(
    paper_id: str,
    kind: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if kind not in {"translation", "summary", "improvement"}:
        raise HTTPException(status_code=400, detail="不支持的内容类型。")

    paper = await _get_user_paper(paper_id, user, db)
    summary_template = paper.summary_template if kind == "summary" else None
    content = storage.read_result(paper_id, kind, summary_template=summary_template)  # type: ignore[arg-type]
    return ContentResponse(paper_id=paper_id, kind=kind, content=content)  # type: ignore[arg-type]


@router.get("/papers/{paper_id}/pdf")
async def get_pdf(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_paper(paper_id, user, db)
    pdf_path = storage.pdf_path(paper_id)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在。")
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/papers/{paper_id}/pdf/download")
async def download_pdf(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    paper = await _get_user_paper(paper_id, user, db)
    pdf_path = storage.pdf_path(paper_id)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在。")

    filename = paper.source_filename or f"{paper_id}.pdf"
    return FileResponse(pdf_path, media_type="application/pdf", filename=filename)


@router.get("/papers/{paper_id}/translation/layout")
async def get_translation_layout(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_paper(paper_id, user, db)
    layout_path = storage.paper_output_dir(paper_id) / "translated_layout.html"
    if not layout_path.exists():
        raise HTTPException(status_code=404, detail="双栏翻译版尚未生成。")
    return FileResponse(
        layout_path,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": "inline"},
    )


@router.post("/papers/{paper_id}/chat", response_model=ChatResponse)
async def chat(
    paper_id: str,
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    paper = await _get_user_paper(paper_id, user, db)
    return await chat_with_paper(paper_id, payload, storage, summary_template=paper.summary_template)


@router.delete("/papers/{paper_id}")
async def delete_paper(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    paper = await _get_user_paper(paper_id, user, db)
    await db.delete(paper)
    return {"message": "论文已删除"}
