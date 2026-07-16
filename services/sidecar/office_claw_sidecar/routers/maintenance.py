"""Maintenance endpoints — on-demand cleanup of temporary files."""

import logging

from fastapi import APIRouter

from office_claw_sidecar.config import cleanup_temp

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/cleanup")
async def cleanup_temp_files():
    """
    Delete all temporary files from excel_uploads/ and document_exports/.

    Unlike the startup cleanup (which keeps files younger than 24 h), this
    endpoint deletes *all* files regardless of age, so users can reclaim disk
    space on demand.

    Returns:
        { deleted_count: int, freed_bytes: int }
    """
    # max_age 미지정 → 나이와 무관하게 모든 임시 파일 삭제 (수동 정리)
    deleted_count, freed_bytes = cleanup_temp()
    return {"deleted_count": deleted_count, "freed_bytes": freed_bytes}
