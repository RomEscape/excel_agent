"""
워크스페이스 파일 접근 엔드포인트 — Phase 1 (Private-Claw).

모든 경로는 sandbox.py를 통해 ~/PrivateClaw/Workspace 내부로 제한된다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from office_claw_sidecar import sandbox

router = APIRouter()


class WriteFileRequest(BaseModel):
    """파일 쓰기 요청 모델."""
    path: str
    content: str


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
