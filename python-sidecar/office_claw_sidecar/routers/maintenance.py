"""Maintenance endpoints — on-demand cleanup of temporary files."""

import logging

from fastapi import APIRouter

from office_claw_sidecar.config import get_data_dir

router = APIRouter()
logger = logging.getLogger(__name__)

_TEMP_SUBDIRS = ("excel_uploads", "document_exports")


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
    deleted_count = 0
    freed_bytes = 0

    for subdir in _TEMP_SUBDIRS:
        temp_dir = get_data_dir() / subdir
        if not temp_dir.exists():
            continue
        for candidate in list(temp_dir.iterdir()):
            if not candidate.is_file():
                continue
            try:
                file_size = candidate.stat().st_size
                candidate.unlink()
                deleted_count += 1
                freed_bytes += file_size
                logger.info("임시 파일 정리 (수동): %s (%d bytes)", candidate, file_size)
            except OSError as exc:
                logger.warning("임시 파일 삭제 실패 (%s): %s", candidate, exc)

    return {"deleted_count": deleted_count, "freed_bytes": freed_bytes}
