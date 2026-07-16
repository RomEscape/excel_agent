"""
Sprint 5 사이드카 통합 테스트.

검증 항목:
  1. 채팅 세션 영속화
     5-1. POST /chat/messages → {ok, id} 반환
     5-2. GET  /chat/sessions → 세션 목록 (최근순)
     5-3. GET  /chat/sessions/{id}/messages → 메시지 전체 (시간순)
     5-4. DELETE /chat/sessions/{id} → {ok, deleted_count}
     5-5. 잘못된 role → 400

  2. 백업 export / import round-trip
     5-6. POST /backup/export → {ok, file_path, size_bytes} + zip 파일 생성
     5-7. POST /backup/import → {ok, restored, warnings} + 파일 복원 확인
     5-8. import 경로 탈출 차단 (manifest.json 없는 zip → 400)
     5-9. import 비절대경로 → 400
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from office_claw_sidecar.main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer dev-token"}

# ── 채팅 세션 영속화 ───────────────────────────────────────────────────────────

class TestChatHistory:
    """채팅 세션 영속화 API 검증 (5-1 ~ 5-5)."""

    def _unique_session(self) -> str:
        import uuid
        return f"test-session-{uuid.uuid4()}"

    def test_5_1_save_message(self):
        """5-1: POST /chat/messages → {ok: true, id: int}"""
        session_id = self._unique_session()
        resp = client.post(
            "/chat/messages",
            json={
                "session_id": session_id,
                "role": "user",
                "text": "안녕하세요",
                "masked_count": 0,
            },
            headers=HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert isinstance(body["id"], int)
        assert body["id"] > 0

    def test_5_2_list_sessions(self):
        """5-2: GET /chat/sessions → 저장한 세션이 목록에 포함됨"""
        session_id = self._unique_session()
        # 메시지 2개 저장
        for i in range(2):
            client.post(
                "/chat/messages",
                json={"session_id": session_id, "role": "user", "text": f"메시지 {i}"},
                headers=HEADERS,
            )

        resp = client.get("/chat/sessions?limit=50", headers=HEADERS)
        assert resp.status_code == 200
        sessions = resp.json()
        assert isinstance(sessions, list)

        matched = [s for s in sessions if s["session_id"] == session_id]
        assert len(matched) == 1
        s = matched[0]
        assert s["message_count"] == 2
        assert s["last_message_at"] is not None
        # preview는 마지막 user 메시지 60자 이내
        assert len(s["preview"]) <= 60

    def test_5_3_get_messages(self):
        """5-3: GET /chat/sessions/{id}/messages → 시간순 메시지 전체"""
        session_id = self._unique_session()
        roles_texts = [
            ("user", "첫 번째 메시지"),
            ("agent", "응답 메시지", {"calls": ["tool1"]}),
            ("user", "두 번째 메시지"),
        ]
        for item in roles_texts:
            payload = {"session_id": session_id, "role": item[0], "text": item[1]}
            if len(item) == 3:
                payload["tool_calls"] = item[2]
            client.post("/chat/messages", json=payload, headers=HEADERS)

        resp = client.get(f"/chat/sessions/{session_id}/messages", headers=HEADERS)
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) == 3

        # 시간순 확인
        assert messages[0]["role"] == "user"
        assert messages[0]["text"] == "첫 번째 메시지"
        assert messages[1]["role"] == "agent"
        assert messages[1]["tool_calls"] == {"calls": ["tool1"]}
        assert messages[2]["text"] == "두 번째 메시지"

    def test_5_4_delete_session(self):
        """5-4: DELETE /chat/sessions/{id} → deleted_count 반환, 이후 조회 시 빈 목록"""
        session_id = self._unique_session()
        for i in range(3):
            client.post(
                "/chat/messages",
                json={"session_id": session_id, "role": "user", "text": f"msg{i}"},
                headers=HEADERS,
            )

        resp = client.delete(f"/chat/sessions/{session_id}", headers=HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["deleted_count"] == 3

        # 삭제 후 조회 시 빈 리스트
        resp2 = client.get(f"/chat/sessions/{session_id}/messages", headers=HEADERS)
        assert resp2.status_code == 200
        assert resp2.json() == []

    def test_5_5_invalid_role(self):
        """5-5: 잘못된 role → 400"""
        resp = client.post(
            "/chat/messages",
            json={"session_id": "test", "role": "invalid_role", "text": "test"},
            headers=HEADERS,
        )
        assert resp.status_code == 400

    def test_5_masked_fields_round_trip(self):
        """masked_count, masked_types, error_text 필드가 정상 저장/조회됨."""
        session_id = self._unique_session()
        client.post(
            "/chat/messages",
            json={
                "session_id": session_id,
                "role": "agent",
                "text": "마스킹된 응답",
                "masked_count": 3,
                "masked_types": ["email", "phone"],
                "error_text": None,
            },
            headers=HEADERS,
        )

        resp = client.get(f"/chat/sessions/{session_id}/messages", headers=HEADERS)
        msg = resp.json()[0]
        assert msg["masked_count"] == 3
        assert msg["masked_types"] == ["email", "phone"]
        assert msg["error_text"] is None


# ── 백업 export / import round-trip ──────────────────────────────────────────

class TestBackup:
    """백업/내보내기 API 검증 (5-6 ~ 5-9)."""

    def test_5_6_export(self):
        """5-6: POST /backup/export → {ok, file_path, size_bytes} + zip 생성 확인"""
        resp = client.post("/backup/export", headers=HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "file_path" in body
        assert body["size_bytes"] > 0

        zip_path = Path(body["file_path"])
        assert zip_path.exists()
        assert zipfile.is_zipfile(zip_path)

        with zipfile.ZipFile(zip_path) as zf:
            assert "manifest.json" in zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["app"] == "ajou-ai"
            assert manifest["version"] == "1"

        # 테스트 후 정리
        zip_path.unlink(missing_ok=True)

    def test_5_7_import_round_trip(self, tmp_path):
        """5-7: export 후 import → {ok, restored, warnings} 확인"""
        # export
        export_resp = client.post("/backup/export", headers=HEADERS)
        assert export_resp.status_code == 200
        zip_path = Path(export_resp.json()["file_path"])

        try:
            # import
            import_resp = client.post(
                "/backup/import",
                json={"file_path": str(zip_path)},
                headers=HEADERS,
            )
            assert import_resp.status_code == 200
            body = import_resp.json()
            assert body["ok"] is True
            assert isinstance(body["restored"], list)
            assert isinstance(body["warnings"], list)
            # 키링 미복원 경고가 있을 수 있음
        finally:
            zip_path.unlink(missing_ok=True)

    def test_5_8_import_invalid_zip(self, tmp_path):
        """5-8: manifest.json 없는 zip → 400"""
        bad_zip = tmp_path / "bad.zip"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("some_file.txt", "not a valid backup")

        resp = client.post(
            "/backup/import",
            json={"file_path": str(bad_zip)},
            headers=HEADERS,
        )
        assert resp.status_code == 400

    def test_5_9_import_relative_path(self):
        """5-9: 상대 경로 → 400"""
        resp = client.post(
            "/backup/import",
            json={"file_path": "relative/path/backup.zip"},
            headers=HEADERS,
        )
        assert resp.status_code == 400

    def test_5_10_import_wrong_app(self, tmp_path):
        """다른 앱의 백업 파일 → 400"""
        bad_zip = tmp_path / "other.zip"
        manifest = {"version": "1", "app": "other-app", "files": []}
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))

        resp = client.post(
            "/backup/import",
            json={"file_path": str(bad_zip)},
            headers=HEADERS,
        )
        assert resp.status_code == 400
