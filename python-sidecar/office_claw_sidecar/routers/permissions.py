"""
권한 설정 라우터 — Phase 3 (officeclaw).

Permission Manager UI를 지원하는 엔드포인트.

엔드포인트:
  GET    /permissions                  — 현재 권한 설정 반환
  PUT    /permissions                  — 권한 설정 저장
  POST   /permissions/whitelist        — 화이트리스트 명령 추가
  DELETE /permissions/whitelist/{cmd}  — 화이트리스트 명령 제거
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from office_claw_sidecar.config import get_data_dir, get_workspace_root
from office_claw_sidecar.services.audit_service import AuditService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["permissions"])
_audit = AuditService()

# 권한 설정 파일 경로
_PERMISSIONS_FILE = Path(get_data_dir()) / "permissions.json"

# 기본 권한 설정
_DEFAULT_PERMISSIONS: dict[str, Any] = {
    "allowed_folders": [str(get_workspace_root())],
    "allowed_apps": ["excel", "email", "document"],
    "shell_command_whitelist": [],
    "python_module_whitelist": [],
}


# ── 요청 모델 ─────────────────────────────────────────────────────────────────

class PermissionsUpdateRequest(BaseModel):
    """권한 설정 전체 업데이트 요청."""
    allowed_folders: list[str]
    allowed_apps: list[str]
    shell_command_whitelist: list[str]
    python_module_whitelist: list[str]


class WhitelistAddRequest(BaseModel):
    """화이트리스트 명령 추가 요청."""
    command: str
    command_type: str = "shell"  # "shell" 또는 "python"
    reason: str = ""


# ── 내부 유틸 ─────────────────────────────────────────────────────────────────

def _load_permissions() -> dict[str, Any]:
    """권한 설정을 파일에서 로드한다. 파일이 없으면 기본값을 반환한다."""
    if not _PERMISSIONS_FILE.exists():
        return dict(_DEFAULT_PERMISSIONS)
    try:
        return json.loads(_PERMISSIONS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[permissions] 설정 파일 로드 실패, 기본값 사용: %s", e)
        return dict(_DEFAULT_PERMISSIONS)


def _save_permissions(data: dict[str, Any]) -> None:
    """권한 설정을 파일에 저장한다."""
    _PERMISSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PERMISSIONS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("")
async def get_permissions() -> dict:
    """
    현재 권한 설정을 반환한다.

    Returns::

        {
            "allowed_folders": [...],
            "allowed_apps": [...],
            "shell_command_whitelist": [...],
            "python_module_whitelist": [...],
        }
    """
    return _load_permissions()


@router.put("")
async def update_permissions(req: PermissionsUpdateRequest) -> dict:
    """
    권한 설정 전체를 업데이트하고 저장한다.

    변경 사항은 즉시 반영되며 permissions.json에 영속된다.
    화이트리스트 변경은 CommandAnalyzer에 즉시 적용된다.
    """
    data = {
        "allowed_folders": req.allowed_folders,
        "allowed_apps": req.allowed_apps,
        "shell_command_whitelist": req.shell_command_whitelist,
        "python_module_whitelist": req.python_module_whitelist,
    }
    try:
        _save_permissions(data)
    except Exception as e:
        logger.error("[permissions] 설정 저장 실패: %s", e)
        raise HTTPException(status_code=500, detail=f"권한 설정 저장 실패: {e}")

    # 화이트리스트를 CommandAnalyzer에 즉시 반영
    try:
        from office_claw_sidecar.analyzer import get_analyzer
        combined_whitelist = req.shell_command_whitelist + req.python_module_whitelist
        get_analyzer().load_whitelist(combined_whitelist)
        logger.info("[permissions] CommandAnalyzer 화이트리스트 갱신: %d개 항목", len(combined_whitelist))
    except Exception as e:
        logger.warning("[permissions] CommandAnalyzer 화이트리스트 갱신 실패: %s", e)

    _audit.log("permissions.updated", "permissions", f"folders={len(req.allowed_folders)} apps={len(req.allowed_apps)}")
    logger.info("[permissions] 권한 설정 업데이트: folders=%d apps=%d", len(req.allowed_folders), len(req.allowed_apps))
    return {"ok": True, **data}


@router.post("/whitelist")
async def add_whitelist_command(req: WhitelistAddRequest) -> dict:
    """
    명령어를 화이트리스트에 추가한다.

    화이트리스트에 등록된 명령은 CommandAnalyzer에서 SAFE로 처리된다.
    변경 사항은 저장 즉시 CommandAnalyzer에 반영된다.
    """
    if not req.command.strip():
        raise HTTPException(status_code=422, detail="명령어를 입력해주세요.")

    data = _load_permissions()
    cmd = req.command.strip()

    if req.command_type == "python":
        whitelist_key = "python_module_whitelist"
    else:
        whitelist_key = "shell_command_whitelist"

    if cmd not in data[whitelist_key]:
        data[whitelist_key].append(cmd)
        _save_permissions(data)
        _audit.log("permissions.whitelist_add", cmd, f"type={req.command_type}")
        logger.info("[permissions] 화이트리스트 추가: %s (%s)", cmd, req.command_type)

    # CommandAnalyzer에 즉시 반영
    try:
        from office_claw_sidecar.analyzer import get_analyzer
        combined = data["shell_command_whitelist"] + data["python_module_whitelist"]
        get_analyzer().load_whitelist(combined)
    except Exception as e:
        logger.warning("[permissions] CommandAnalyzer 화이트리스트 갱신 실패: %s", e)

    return {"ok": True, "command": cmd, "command_type": req.command_type, "whitelist": data[whitelist_key]}


@router.delete("/whitelist/{command:path}")
async def remove_whitelist_command(command: str) -> dict:
    """
    화이트리스트에서 명령어를 제거한다.

    URL 인코딩된 명령어를 지원하기 위해 path 파라미터를 사용한다.
    """
    data = _load_permissions()
    cmd = command.strip()
    removed = False

    for key in ("shell_command_whitelist", "python_module_whitelist"):
        if cmd in data[key]:
            data[key].remove(cmd)
            removed = True

    if not removed:
        raise HTTPException(status_code=404, detail=f"화이트리스트에서 '{cmd}'을 찾을 수 없습니다.")

    _save_permissions(data)
    _audit.log("permissions.whitelist_remove", cmd)
    logger.info("[permissions] 화이트리스트 제거: %s", cmd)

    # CommandAnalyzer에 즉시 반영
    try:
        from office_claw_sidecar.analyzer import get_analyzer
        combined = data["shell_command_whitelist"] + data["python_module_whitelist"]
        get_analyzer().load_whitelist(combined)
    except Exception as e:
        logger.warning("[permissions] CommandAnalyzer 화이트리스트 갱신 실패: %s", e)

    return {"ok": True, "command": cmd}
