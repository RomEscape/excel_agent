"""Excel AI endpoints — file upload, AI analysis, report generation, and export."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from office_claw_sidecar.config import get_data_dir
from office_claw_sidecar.services import excel_service
from office_claw_sidecar.services.llm_service import get_llm_service, LLMService

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Temp storage ────────────────────────────────────────────────────────────

def _temp_dir() -> Path:
    """Return (and create if needed) the temp directory for uploaded Excel files."""
    d = get_data_dir() / "excel_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_file(file_id: str) -> Path:
    """Resolve a file_id to its full path, raising 404 if not found."""
    base = _temp_dir()
    # Scan for any file whose stem == file_id
    for candidate in base.iterdir():
        if candidate.stem == file_id:
            return candidate
    raise HTTPException(status_code=404, detail=f"파일을 찾을 수 없습니다: {file_id}")


# ── Request / response models ───────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    file_id: str
    question: str


class FileIdRequest(BaseModel):
    file_id: str


class ExportRequest(BaseModel):
    file_id: str
    report_markdown: str


# ── Endpoints ───────────────────────────────────────────────────────────────

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/upload")
async def upload_excel(file: UploadFile = File(...)):
    """
    Accept an xlsx or csv file upload, save it to the temp directory,
    and return its file_id and parsed metadata.
    """
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in (".xlsx", ".xls", ".csv"):
        raise HTTPException(
            status_code=400,
            detail="지원하지 않는 파일 형식입니다. xlsx 또는 csv 파일을 업로드하세요.",
        )

    # Check file size before reading into disk
    if file.size is not None and file.size > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail="파일 크기가 50MB를 초과합니다. 더 작은 파일을 사용해 주세요.",
        )

    file_id = uuid.uuid4().hex
    save_path = _temp_dir() / f"{file_id}{suffix}"

    try:
        with save_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"파일 저장 실패: {exc}")
    finally:
        await file.close()

    # Secondary size check for cases where Content-Length was absent
    actual_size = save_path.stat().st_size
    if actual_size > _MAX_UPLOAD_BYTES:
        save_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="파일 크기가 50MB를 초과합니다. 더 작은 파일을 사용해 주세요.",
        )

    try:
        meta = excel_service.parse_file(str(save_path))
    except Exception as exc:
        save_path.unlink(missing_ok=True)
        logger.exception("Excel 파싱 오류: %s", exc)
        raise HTTPException(
            status_code=422,
            detail=f"파일을 읽을 수 없습니다. 올바른 엑셀/CSV 파일인지 확인하세요. ({exc})",
        )

    return {"file_id": file_id, "filename": file.filename, **meta}


@router.post("/analyze")
async def analyze_excel(req: AnalyzeRequest, llm: LLMService = Depends(get_llm_service)):
    """Answer a natural-language question about the uploaded spreadsheet."""
    file_path = _resolve_file(req.file_id)
    try:
        answer = await excel_service.analyze_with_ai(str(file_path), req.question, llm)
    except Exception as exc:
        logger.exception("Excel 분석 오류: %s", exc)
        raise HTTPException(status_code=500, detail=f"AI 분석 중 오류가 발생했습니다: {exc}")
    return {"answer": answer}


@router.post("/report")
async def generate_report(req: FileIdRequest, llm: LLMService = Depends(get_llm_service)):
    """Auto-generate a comprehensive Korean data analysis report."""
    file_path = _resolve_file(req.file_id)
    try:
        report = await excel_service.generate_report(str(file_path), llm)
    except Exception as exc:
        logger.exception("리포트 생성 오류: %s", exc)
        raise HTTPException(status_code=500, detail=f"리포트 생성 중 오류가 발생했습니다: {exc}")
    return {"report": report}


@router.post("/formulas")
async def suggest_formulas(req: FileIdRequest, llm: LLMService = Depends(get_llm_service)):
    """Suggest useful Excel formulas based on the file's column structure."""
    file_path = _resolve_file(req.file_id)
    try:
        meta = excel_service.parse_file(str(file_path))
        column_info = {
            "columns": meta["columns"],
            "dtypes": meta["dtypes"],
            "numeric_columns": meta["numeric_columns"],
        }
        suggestions = await excel_service.suggest_formulas(column_info, llm)
    except Exception as exc:
        logger.exception("수식 제안 오류: %s", exc)
        raise HTTPException(status_code=500, detail=f"수식 제안 중 오류가 발생했습니다: {exc}")
    return {"suggestions": suggestions}


@router.get("/chart-data")
async def get_chart_data(file_id: str, sheet_name: str = ""):
    """Return numeric column data as Chart.js-ready JSON."""
    file_path = _resolve_file(file_id)
    # Derive the active sheet if not provided
    if not sheet_name:
        try:
            meta = excel_service.parse_file(str(file_path))
            sheet_name = meta.get("active_sheet", "Sheet1")
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    try:
        chart_data = excel_service.extract_chart_data(str(file_path), sheet_name)
    except Exception as exc:
        logger.exception("차트 데이터 추출 오류: %s", exc)
        raise HTTPException(status_code=500, detail=f"차트 데이터 추출 중 오류가 발생했습니다: {exc}")
    return chart_data


@router.post("/export")
async def export_report(req: ExportRequest):
    """Append an AI report sheet to the workbook and return the file for download."""
    file_path = _resolve_file(req.file_id)
    try:
        out_path = excel_service.export_report_to_excel(str(file_path), req.report_markdown)
    except Exception as exc:
        logger.exception("Excel 내보내기 오류: %s", exc)
        raise HTTPException(status_code=500, detail=f"Excel 내보내기 중 오류가 발생했습니다: {exc}")

    return FileResponse(
        path=out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=Path(out_path).name,
    )
