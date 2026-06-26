"""
backup.py — Sprint 5 백업/내보내기.

export: audit.db + 주요 JSON 파일 + keyring 키 목록(값 제외) → zip
import: zip 검증 → 기존 파일 .bak 백업 → 복원

zip 내부 구조:
  manifest.json          (버전, 생성 일시, 포함 파일 목록)
  audit.db               (명령 감사 + 채팅 세션 DB)
  data/permissions.json  (선택적)
  data/skill_whitelist.json
  data/credentials_registry.json
  data/audit.jsonl
  keyring_keys.json      (키 이름 목록만, 값 없음)

보안: import 시 zip 경로 탈출 차단 (경로 정규화 후 대상 디렉토리 내부 확인)
"""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from office_claw_sidecar.config import (
    get_app_db_path,
    get_data_dir,
)

logger = logging.getLogger(__name__)

# command_audit / chat_history와 같은 DB 파일 — config.get_app_db_path()가 단일 출처
_DB_PATH = get_app_db_path()
_MANIFEST_VERSION = "1"

# export에 포함할 data_dir 내 파일 목록 (없어도 건너뜀)
_DATA_FILES = [
    "permissions.json",
    "skill_whitelist.json",
    "credentials_registry.json",
    "audit.jsonl",
]


def _downloads_dir() -> Path:
    """사용자 Downloads 폴더. 없으면 홈 폴더로 fallback."""
    dl = Path.home() / "Downloads"
    return dl if dl.exists() else Path.home()


def _timestamp_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _get_keyring_keys() -> list[str]:
    """OS 키링에 등록된 키 이름 목록만 반환한다 (값 제외)."""
    try:
        from office_claw_sidecar.services.keyring_service import KeyringService
        return KeyringService().list_keys()
    except Exception as exc:
        logger.warning("키링 키 목록 조회 실패 (무시됨): %s", exc)
        return []


# ── Export ───────────────────────────────────────────────────────────────────

def export_backup() -> dict:
    """백업 zip을 생성하고 {ok, file_path, size_bytes}를 반환한다."""
    tag = _timestamp_tag()
    zip_name = f"ajou-ai-backup-{tag}.zip"
    zip_path = _downloads_dir() / zip_name

    data_dir = get_data_dir()
    included: list[str] = []

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. audit.db
        if _DB_PATH.exists():
            zf.write(_DB_PATH, "audit.db")
            included.append("audit.db")
        else:
            logger.info("audit.db 없음 — 건너뜀")

        # 2. data_dir 내 주요 파일
        for fname in _DATA_FILES:
            src = data_dir / fname
            if src.exists():
                zf.write(src, f"data/{fname}")
                included.append(f"data/{fname}")

        # 3. keyring 키 이름 목록 (값은 보안상 제외)
        keyring_keys = _get_keyring_keys()
        zf.writestr(
            "keyring_keys.json",
            json.dumps(
                {"keys": keyring_keys, "note": "키 이름만 저장됨. 새 PC에서 값을 재입력하세요."},
                ensure_ascii=False,
                indent=2,
            ),
        )
        included.append("keyring_keys.json")

        # 4. manifest
        manifest = {
            "version": _MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app": "ajou-ai",
            "files": included,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    size_bytes = zip_path.stat().st_size
    logger.info("백업 완료: %s (%d bytes)", zip_path, size_bytes)
    return {"ok": True, "file_path": str(zip_path), "size_bytes": size_bytes}


# ── Import ───────────────────────────────────────────────────────────────────

def _validate_zip_path(member_name: str, target_dir: Path) -> Path:
    """zip 멤버 경로를 검증하고 최종 절대 경로를 반환한다.

    경로 탈출(path traversal) 공격 차단:
    - '..' 컴포넌트 포함 시 ValueError 발생
    - 최종 경로가 target_dir 밖이면 ValueError 발생
    """
    # 절대 경로 또는 '..' 포함 차단
    if member_name.startswith("/") or member_name.startswith("\\"):
        raise ValueError(f"절대 경로 zip 멤버 거부: {member_name}")

    candidate = (target_dir / member_name).resolve()
    try:
        candidate.relative_to(target_dir.resolve())
    except ValueError:
        raise ValueError(f"경계 탈출 zip 멤버 거부: {member_name}")
    return candidate


def import_backup(file_path: str) -> dict:
    """zip을 검증하고 파일을 복원한다.

    반환: {ok, restored: [...], warnings: [...]}
    """
    src = Path(file_path)
    if not src.exists():
        raise FileNotFoundError(f"zip 파일을 찾을 수 없습니다: {file_path}")
    if not zipfile.is_zipfile(src):
        raise ValueError("유효하지 않은 zip 파일입니다.")

    tag = _timestamp_tag()
    restored: list[str] = []
    warnings: list[str] = []

    with zipfile.ZipFile(src, "r") as zf:
        # 1. manifest 검증
        if "manifest.json" not in zf.namelist():
            raise ValueError("manifest.json이 없습니다. 올바른 ajou-ai 백업 파일이 아닙니다.")

        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        if manifest.get("app") != "ajou-ai":
            raise ValueError("이 백업 파일은 ajou-ai 앱용이 아닙니다.")

        # 2. 모든 멤버 경로 사전 검증 (임시 디렉토리 기준)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_dir = Path(tmp_str)
            for member in zf.namelist():
                if member == "manifest.json":
                    continue
                try:
                    _validate_zip_path(member, tmp_dir)
                except ValueError as exc:
                    raise ValueError(f"zip 내 위험한 경로 감지: {exc}")

        # 3. audit.db 복원 (덮어쓰기, 기존은 .bak 백업)
        if "audit.db" in zf.namelist():
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            if _DB_PATH.exists():
                bak = _DB_PATH.with_suffix(f".bak.{tag}")
                shutil.copy2(_DB_PATH, bak)
                warnings.append(f"기존 audit.db를 {bak.name}으로 백업했습니다.")
            _DB_PATH.write_bytes(zf.read("audit.db"))
            restored.append("audit.db")

        # 4. data/ 파일 복원
        data_dir = get_data_dir()
        for member in zf.namelist():
            if not member.startswith("data/"):
                continue
            fname = member[len("data/"):]
            if not fname or fname not in _DATA_FILES:
                continue
            dest = data_dir / fname
            if dest.exists():
                bak = dest.with_suffix(f".bak.{tag}")
                shutil.copy2(dest, bak)
                warnings.append(f"기존 {fname}을 {bak.name}으로 백업했습니다.")
            dest.write_bytes(zf.read(member))
            restored.append(f"data/{fname}")

        # 5. keyring 키 처리 (목록만 있으므로 경고 추가)
        if "keyring_keys.json" in zf.namelist():
            keyring_data = json.loads(zf.read("keyring_keys.json").decode("utf-8"))
            keys = keyring_data.get("keys", [])
            if keys:
                warnings.append(
                    f"키링 자격증명({len(keys)}개)은 보안상 백업에 포함되지 않습니다. "
                    f"다음 키를 새 PC에서 재입력하세요: {', '.join(keys)}"
                )

    logger.info("백업 복원 완료: %d개 파일, 경고 %d개", len(restored), len(warnings))
    return {"ok": True, "restored": restored, "warnings": warnings}
