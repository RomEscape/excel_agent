"""
워크스페이스 라우터 — 파일 삭제 엔드포인트 테스트.

홈 화면(최종 와이어프레임 B-1)의 `문서 삭제` 액션이 쓰는 경로다. 되돌릴 수 없는
동작이라 경계 조건을 고정해 둔다:

  - 워크스페이스 밖 경로는 지우지 않는다 (경로 탈출 차단)
  - 디렉토리는 지우지 않는다 (하위 내용을 통째로 날리는 동작이라 별도 취급)
  - 없는 파일은 404로 알린다 (조용히 성공하면 UI가 지워졌다고 거짓말한다)
"""

from pathlib import Path

from fastapi.testclient import TestClient

from office_claw_sidecar import sandbox
from office_claw_sidecar.main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer dev-token"}


def _make_file(name: str, content: str = "hello") -> None:
    target = sandbox.WORKSPACE_ROOT / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class TestDeleteFile:
    def test_deletes_existing_file(self):
        _make_file("삭제대상.txt")
        assert (sandbox.WORKSPACE_ROOT / "삭제대상.txt").exists()

        resp = client.delete(
            "/workspace/file", params={"path": "삭제대상.txt"}, headers=HEADERS
        )

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert not (sandbox.WORKSPACE_ROOT / "삭제대상.txt").exists()

    def test_missing_file_returns_404(self):
        # 조용히 성공하면 UI가 "지웠습니다"라고 거짓말하게 된다.
        resp = client.delete(
            "/workspace/file", params={"path": "없는파일.xlsx"}, headers=HEADERS
        )
        assert resp.status_code == 404

    def test_directory_is_rejected(self):
        target = sandbox.WORKSPACE_ROOT / "폴더삭제시도"
        target.mkdir(parents=True, exist_ok=True)

        resp = client.delete(
            "/workspace/file", params={"path": "폴더삭제시도"}, headers=HEADERS
        )

        assert resp.status_code == 400
        assert target.exists(), "디렉토리는 남아 있어야 한다"
        target.rmdir()

    def test_path_escape_is_rejected(self):
        # 워크스페이스 밖으로 나가는 경로는 파일이 실제로 있든 없든 거부돼야 한다.
        resp = client.delete(
            "/workspace/file",
            params={"path": "../../.zshrc"},
            headers=HEADERS,
        )
        assert resp.status_code in (403, 404)

    def test_empty_path_is_rejected(self):
        resp = client.delete("/workspace/file", params={"path": "   "}, headers=HEADERS)
        assert resp.status_code == 400


# ─── 이하: 워크스페이스 루트 경로 회귀 테스트(데모 브랜치) ───

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
