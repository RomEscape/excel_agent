"""
routers/backup.py — Sprint 5 백업/내보내기 API.

엔드포인트:
  POST /backup/export   백업 zip 생성
  POST /backup/import   백업 zip 복원
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from office_claw_sidecar import backup

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/export")
def post_export():
    """현재 데이터를 ~/Downloads/ajou-ai-backup-{timestamp}.zip으로 내보낸다."""
    try:
        result = backup.export_backup()
        return result
    except Exception as exc:
        logger.error("백업 export 실패: %s", exc)
        raise HTTPException(status_code=500, detail=f"백업 export 실패: {exc}")


class ImportRequest(BaseModel):
    file_path: str


@router.post("/import")
def post_import(req: ImportRequest):
    """지정된 zip 파일로부터 데이터를 복원한다."""
    # 기본 경로 검증 — zip 파일 경로는 로컬 파일이어야 함
    path = Path(req.file_path)
    if not path.is_absolute():
        raise HTTPException(status_code=400, detail="절대 경로를 사용해야 합니다.")
    if path.suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail=".zip 파일만 허용됩니다.")

    try:
        result = backup.import_backup(req.file_path)
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("백업 import 실패: %s", exc)
        raise HTTPException(status_code=500, detail=f"백업 import 실패: {exc}")
