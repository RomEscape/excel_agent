"""Document AI endpoints — Korean document generation and Word/PDF export."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from office_claw_sidecar.config import get_data_dir
from office_claw_sidecar.services import document_service
from office_claw_sidecar.services.llm_service import get_llm_service, LLMService

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Temp storage ────────────────────────────────────────────────────────────

def _temp_dir() -> Path:
    d = get_data_dir() / "document_exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Request models ──────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    doc_type: str
    content: str
    tone: str = "공식적"
    length: str = "보통"


class ExportRequest(BaseModel):
    title: str
    content: str


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/generate")
async def generate_document(req: GenerateRequest, llm: LLMService = Depends(get_llm_service)):
    """
    Generate a Korean document draft.

    Body: { doc_type, content, tone, length }
    Returns: { draft: markdown_string }
    """
    try:
        draft = await document_service.generate_document(
            doc_type=req.doc_type,
            content=req.content,
            tone=req.tone,
            length=req.length,
            llm_service=llm,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("문서 생성 오류: %s", exc)
        raise HTTPException(status_code=500, detail=f"문서 생성 중 오류가 발생했습니다: {exc}")
    return {"draft": draft}


@router.post("/export/docx")
async def export_docx(req: ExportRequest):
    """
    Convert markdown content to a Word document and return it as a download.
    """
    try:
        out_path = document_service.export_to_docx(
            title=req.title,
            markdown_content=req.content,
            output_dir=_temp_dir(),
        )
    except Exception as exc:
        logger.exception("Word 내보내기 오류: %s", exc)
        raise HTTPException(status_code=500, detail=f"Word 파일 생성 중 오류가 발생했습니다: {exc}")

    return FileResponse(
        path=out_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=Path(out_path).name,
    )


@router.post("/export/pdf")
async def export_pdf(req: ExportRequest):
    """
    Convert markdown content to a PDF file and return it as a download.

    Returns HTTP 422 with a user-friendly Korean message if no Korean font
    is available on the system (prevents delivering a garbled PDF).
    """
    try:
        out_path = document_service.export_to_pdf(
            title=req.title,
            markdown_content=req.content,
            output_dir=_temp_dir(),
        )
    except ValueError as exc:
        # Font not found — surface as a clear user-facing error (not 500)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("PDF 내보내기 오류: %s", exc)
        raise HTTPException(status_code=500, detail=f"PDF 파일 생성 중 오류가 발생했습니다: {exc}")

    return FileResponse(
        path=out_path,
        media_type="application/pdf",
        filename=Path(out_path).name,
    )
