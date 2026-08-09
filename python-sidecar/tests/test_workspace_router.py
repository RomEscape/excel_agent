"""워크스페이스 라우터 테스트."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from office_claw_sidecar import sandbox
from office_claw_sidecar.main import app

HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)


def test_root_returns_the_absolute_workspace_path():
    """
    사이드카 바깥(Rust IPC)이 이 값을 그대로 받아 파일을 연다.
    상대 경로나 '~' 축약을 돌려주면 여는 쪽이 엉뚱한 폴더를 뒤지게 된다.
    """
    resp = client.get("/workspace/root", headers=HEADERS)

    assert resp.status_code == 200
    root = resp.json()["root"]
    assert Path(root).is_absolute()
    assert not root.startswith("~")


def test_root_matches_the_path_used_to_list_files():
    """루트를 알려주는 곳과 파일을 실제로 읽고 쓰는 곳이 같아야 한다."""
    root = client.get("/workspace/root", headers=HEADERS).json()["root"]
    listed = client.get("/workspace/files", headers=HEADERS).json()["workspace"]

    assert Path(root) == Path(listed) == Path(str(sandbox.WORKSPACE_ROOT))
