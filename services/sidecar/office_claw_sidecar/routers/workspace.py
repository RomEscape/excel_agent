"""
워크스페이스 파일 접근 엔드포인트 — Phase 1 (officeclaw).

모든 경로는 sandbox.py를 통해 config.get_workspace_root() 내부로 제한된다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from openpyxl import Workbook
from pydantic import BaseModel

from office_claw_sidecar import sandbox

router = APIRouter()


class WriteFileRequest(BaseModel):
    """파일 쓰기 요청 모델."""
    path: str
    content: str


class CreateExcelFileRequest(BaseModel):
    """새 엑셀 파일 생성 요청 모델."""
    path: str
    sheet_name: str | None = "Sheet1"


@router.get("/root")
async def get_root():
    """
    워크스페이스 루트의 절대 경로를 반환한다.

    워크스페이스 위치를 계산하는 곳은 config.get_workspace_root() 하나뿐이다.
    파일을 여는 Rust IPC처럼 사이드카 바깥에서 같은 폴더를 가리켜야 하는 쪽은
    경로를 다시 조합하지 말고 이 값을 받아 쓴다.

    Returns
    -------
    {"root": str}
    """
    return {"root": str(sandbox.WORKSPACE_ROOT)}


@router.get("/files")
async def list_files(path: str = Query(default="", description="워크스페이스 기준 상대 경로")):
    """
    워크스페이스 내 파일 목록을 반환한다.

    Parameters
    ----------
    path:
        워크스페이스 기준 상대 경로 (기본값: 루트).

    Returns
    -------
    {"files": [{name, path, size, modified, is_dir}, ...], "workspace": str}

    Raises
    ------
    403: 워크스페이스 외부 경로
    404: 경로가 존재하지 않음
    400: 경로가 디렉토리가 아님
    """
    try:
        entries = sandbox.list_files(path)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파일 목록 조회 실패: {e}")

    # `.gitkeep` 같은 숨김 파일은 사용자 작업물이 아니다 — 목록에 떠서 "이건 뭐지"가 됐다(2026-09-06).
    entries = [e for e in entries if not str(e.get("name", "")).startswith(".")]
    return {
        "files": entries,
        "workspace": str(sandbox.WORKSPACE_ROOT),
    }


@router.get("/file")
async def read_file(path: str = Query(..., description="워크스페이스 기준 상대 경로")):
    """
    워크스페이스 내 파일 내용을 반환한다.

    Parameters
    ----------
    path:
        워크스페이스 기준 상대 경로.

    Returns
    -------
    {"path": str, "content": str, "size": int}

    Raises
    ------
    403: 워크스페이스 외부 경로
    404: 파일이 존재하지 않음
    400: 경로가 디렉토리임
    """
    try:
        content = sandbox.read_file(path)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IsADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파일 읽기 실패: {e}")

    return {
        "path": path,
        "content": content,
        "size": len(content.encode("utf-8")),
    }


@router.post("/file")
async def write_file(req: WriteFileRequest):
    """
    워크스페이스 내 파일에 내용을 쓴다.
    중간 디렉토리가 없으면 자동 생성된다.

    Request body:
        path: 워크스페이스 기준 상대 경로
        content: 저장할 텍스트

    Returns
    -------
    {"ok": true, "path": str}

    Raises
    ------
    403: 워크스페이스 외부 경로
    """
    try:
        sandbox.write_file(req.path, req.content)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파일 쓰기 실패: {e}")

    return {"ok": True, "path": req.path}


@router.delete("/file")
async def delete_file(path: str = Query(..., description="워크스페이스 기준 상대 경로")):
    """
    워크스페이스 내 파일 하나를 삭제한다.

    홈 화면의 `문서 삭제` 액션이 쓴다.

    디렉토리는 받지 않는다 — 폴더 삭제는 하위 내용을 통째로 날리는 동작이라
    파일 삭제와 같은 확인 절차로 묶을 수 없다. 필요해지면 별도 엔드포인트로
    낸다.

    Parameters
    ----------
    path:
        워크스페이스 기준 상대 경로.

    Returns
    -------
    {"ok": true, "path": str}

    Raises
    ------
    400: 경로가 비었거나 디렉토리임
    403: 워크스페이스 외부 경로
    404: 파일이 존재하지 않음
    """
    raw_path = str(path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="파일 경로가 비어 있습니다.")

    try:
        if not sandbox.is_path_safe(raw_path):
            raise PermissionError(f"접근 거부: 워크스페이스 외부 경로입니다. ({raw_path})")

        resolved = (sandbox.WORKSPACE_ROOT / Path(raw_path)).resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {raw_path}")
        if resolved.is_dir():
            raise IsADirectoryError(f"디렉토리는 삭제할 수 없습니다: {raw_path}")

        resolved.unlink()
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IsADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파일 삭제 실패: {e}")

    return {"ok": True, "path": raw_path}


@router.post("/excel-file")
async def create_excel_file(req: CreateExcelFileRequest):
    """
    워크스페이스 내 새 .xlsx 파일을 생성한다.

    Request body:
        path: 워크스페이스 기준 상대 경로 (예: reports/새파일.xlsx)
        sheet_name: 첫 시트명 (선택, 기본값: Sheet1)

    Returns
    -------
    {"ok": true, "path": str, "sheet_name": str}

    Raises
    ------
    400: 잘못된 파일명/경로
    403: 워크스페이스 외부 경로
    409: 같은 이름 파일이 이미 존재
    """
    raw_path = str(req.path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="파일 경로가 비어 있습니다.")

    if not raw_path.lower().endswith(".xlsx"):
        raw_path = f"{raw_path}.xlsx"

    try:
        if not sandbox.is_path_safe(raw_path):
            raise PermissionError(f"접근 거부: 워크스페이스 외부 경로입니다. ({raw_path})")

        resolved = (sandbox.WORKSPACE_ROOT / Path(raw_path)).resolve()
        if resolved.exists():
            raise FileExistsError(f"같은 이름 파일이 이미 존재합니다: {raw_path}")

        resolved.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        safe_sheet_name = str(req.sheet_name or "Sheet1").strip()[:31] or "Sheet1"
        ws.title = safe_sheet_name
        wb.save(resolved)

        rel = resolved.relative_to(sandbox.WORKSPACE_ROOT.resolve())
        return {"ok": True, "path": str(rel), "sheet_name": safe_sheet_name}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"엑셀 파일 생성 실패: {e}")
