"""2026-09-06 강건성 감사 후속 — 사용자가 Excel 에서 직접 타자한 뒤의 경로.

감사가 실측으로 확인한 다섯 가지를 핀으로 굳힌다.
  (a) 다이제스트가 workbook_id=null 에서 머리글·예시행을 비운 채 돌려주던 것
  (b) 20초 캐시가 사용자의 Excel 타자를 무효화하지 않던 것
  (c) COM 쓰기 거부 폴백이 편집 가능한 통합문서를 저장 없이 닫던 것(데이터 손실)
  (d) 셀 편집 모드·대화상자(RPC_E_CALL_REJECTED)에 처리·안내가 없던 것
  (f) 엔진 프로브 5초 창에서 틀린 엔진을 고르던 것
"""

from __future__ import annotations

import time

import pytest

from office_claw_sidecar.services import excel_workbook_digest as digest_mod
from office_claw_sidecar.services.excel_live_service import ExcelLiveError, ExcelLiveService
from office_claw_sidecar.services.excel_readonly_bridge import (
    looks_like_com_busy,
    looks_like_com_write_refusal,
)


# ── 공통 가짜 엔진 ────────────────────────────────────────────────────────────
class _FakeEngine:
    """두 엔진의 공개 계약 중 다이제스트가 쓰는 부분만 흉내 낸다."""

    def __init__(self, engine: str = "xlwings", *, require_workbook_id: bool = True):
        self.engine = engine
        self._require_workbook_id = require_workbook_id
        self.selected: str | None = None
        self.active_path = r"C:\work\typed.xlsx"
        self.values = [["지역", "주문건수"], ["수도권", 10452]]
        self.used_range = "A1:B2"
        self.read_calls: list[tuple[str | None, str, str]] = []

    # 지목이 없어도 활성 통합문서로 폴백하는 쪽(라이브 엔진의 _resolve_workbook)
    def list_sheets(self, workbook_id):
        return {"sheets": ["Sheet1"], "active_sheet": "Sheet1"}

    def get_used_range_ref(self, workbook_id, sheet_name):
        return self.used_range

    def get_selected_workbook_id(self):
        return self.selected

    def get_workbook_path(self, workbook_id=None):
        return self.active_path

    def list_workbooks(self):
        return [{"workbook_id": self.active_path, "name": "typed.xlsx"}]

    # 지목이 없으면 거절하는 쪽(라이브 엔진의 read_range)
    def read_range(self, workbook_id, sheet_name, range_ref):
        self.read_calls.append((workbook_id, sheet_name, range_ref))
        if self._require_workbook_id and not workbook_id:
            raise ExcelLiveError("선택된 통합문서가 없습니다.")
        return {"values": [list(r) for r in self.values], "address": range_ref}


@pytest.fixture(autouse=True)
def _clear_digest_cache():
    digest_mod._digest_cache.clear()
    yield
    digest_mod._digest_cache.clear()


# ── (a) 다이제스트가 머리글을 못 보던 것 ──────────────────────────────────────
class TestDigestSeesTypedHeaders:
    """프론트는 workbook_id 를 항상 null 로 보낸다. 그 기본 경로에서 머리글이 비면
    플래너는 사용자가 방금 타자한 열 이름을 모른 채 파라미터를 추측한다."""

    def test_null_workbook_id_still_reads_headers(self):
        engine = _FakeEngine()
        out = digest_mod.build_workbook_digest(engine, workbook_id=None, use_cache=False)
        sheet = out["sheets"][0]
        headers = [c["header"] for c in sheet["columns"]]
        assert headers == ["지역", "주문건수"], out
        assert sheet["sample_rows"] == [["수도권", "10452"]], sheet
        # read_range 에 **구체적인 통합문서**가 전달됐다 — 이게 없으면 라이브 엔진이 거절한다.
        assert engine.read_calls and engine.read_calls[0][0] == engine.active_path

    def test_selected_workbook_wins_over_active(self):
        engine = _FakeEngine()
        engine.selected = r"C:\work\선택한.xlsx"
        digest_mod.build_workbook_digest(engine, workbook_id=None, use_cache=False)
        assert engine.read_calls[0][0] == r"C:\work\선택한.xlsx"

    def test_explicit_workbook_id_is_untouched(self):
        engine = _FakeEngine()
        engine.selected = r"C:\work\선택한.xlsx"
        digest_mod.build_workbook_digest(engine, workbook_id=r"C:\work\지목.xlsx", use_cache=False)
        assert engine.read_calls[0][0] == r"C:\work\지목.xlsx"

    def test_resolution_failure_falls_back_quietly(self):
        """조회가 전부 실패해도 다이제스트는 죽지 않는다 — 예전 동작으로 떨어진다."""

        class _Broken(_FakeEngine):
            def get_selected_workbook_id(self):
                raise RuntimeError("COM 죽음")

            def get_workbook_path(self, workbook_id=None):
                raise RuntimeError("COM 죽음")

            def list_workbooks(self):
                raise RuntimeError("COM 죽음")

        assert digest_mod._resolve_digest_workbook_id(_Broken(), None) is None


# ── (b) 캐시가 사용자 타자를 못 따라가던 것 ───────────────────────────────────
class TestDigestCacheTtlByEngine:
    """라이브 엔진은 사용자가 언제든 타자한다 — 20초 캐시는 그 변화를 가린다."""

    def test_live_engine_uses_short_ttl(self, monkeypatch):
        engine = _FakeEngine("xlwings")
        base = 1000.0
        monkeypatch.setattr(digest_mod.time, "monotonic", lambda: base)
        digest_mod.build_workbook_digest(engine, workbook_id=None)
        key = engine.active_path
        expires_at = digest_mod._digest_cache[key][0]
        assert expires_at == pytest.approx(base + digest_mod._LIVE_CACHE_TTL_SECONDS)
        assert digest_mod._LIVE_CACHE_TTL_SECONDS < digest_mod._CACHE_TTL_SECONDS

    def test_file_engine_keeps_long_ttl(self, monkeypatch):
        engine = _FakeEngine("file")
        base = 1000.0
        monkeypatch.setattr(digest_mod.time, "monotonic", lambda: base)
        digest_mod.build_workbook_digest(engine, workbook_id=None)
        expires_at = digest_mod._digest_cache[engine.active_path][0]
        assert expires_at == pytest.approx(base + digest_mod._CACHE_TTL_SECONDS)

    def test_live_cache_expires_so_typing_is_seen(self, monkeypatch):
        """짧은 TTL 이 지나면 사용자가 그 사이 친 열이 보인다."""
        engine = _FakeEngine("xlwings")
        clock = {"t": 1000.0}
        monkeypatch.setattr(digest_mod.time, "monotonic", lambda: clock["t"])
        first = digest_mod.build_workbook_digest(engine, workbook_id=None)
        assert [c["header"] for c in first["sheets"][0]["columns"]] == ["지역", "주문건수"]

        # 사용자가 Excel 에서 열을 하나 더 친다.
        engine.values = [["지역", "주문건수", "반품건수"], ["수도권", 10452, 12]]
        engine.used_range = "A1:C2"

        clock["t"] += digest_mod._LIVE_CACHE_TTL_SECONDS + 0.1
        second = digest_mod.build_workbook_digest(engine, workbook_id=None)
        assert [c["header"] for c in second["sheets"][0]["columns"]] == ["지역", "주문건수", "반품건수"]


# ── (c) 저장 안 된 편집을 버리는 닫기 ─────────────────────────────────────────
class _FakeApi:
    def __init__(self, saved: bool | None, full_name: str):
        self.Saved = saved
        self.FullName = full_name


class _FakeBook:
    def __init__(self, saved: bool | None = True, *, save_raises: bool = False):
        self.api = _FakeApi(saved, r"C:\work\typed.xlsx")
        self.closed = False
        self.saved_calls = 0
        self._save_raises = save_raises

    def save(self):
        self.saved_calls += 1
        if self._save_raises:
            raise RuntimeError("Excel 이 저장을 거부")
        self.api.Saved = True

    def close(self):
        self.closed = True


def _service_with(book: _FakeBook) -> ExcelLiveService:
    svc = ExcelLiveService.__new__(ExcelLiveService)
    svc._resolve_workbook = lambda workbook_id=None: book  # type: ignore[method-assign]
    svc.get_workbook_path = lambda workbook_id=None: r"C:\work\typed.xlsx"  # type: ignore[method-assign]
    return svc


class TestCloseWithoutSavingProtectsTypedValues:
    """`close_workbook_without_saving` 은 COM 쓰기 거부 폴백이 상태 확인 없이 부른다.
    편집 가능한 통합문서에 미저장 타자가 있으면 그 값이 사라졌다(2026-09-06 감사)."""

    def test_unsaved_changes_are_saved_before_closing(self):
        book = _FakeBook(saved=False)
        out = _service_with(book).close_workbook_without_saving(None)
        assert book.saved_calls == 1, "닫기 전에 저장하지 않았다"
        assert book.closed is True
        assert out == r"C:\work\typed.xlsx"

    def test_refuses_to_close_when_save_fails(self):
        """저장이 안 되면 닫지 않는다 — 닫는 순간 사용자의 작업이 사라진다."""
        book = _FakeBook(saved=False, save_raises=True)
        with pytest.raises(ExcelLiveError) as err:
            _service_with(book).close_workbook_without_saving(None)
        assert "저장" in str(err.value)
        assert book.closed is False, "저장 실패인데도 닫았다 — 데이터 손실 경로"

    def test_saved_workbook_closes_without_extra_save(self):
        book = _FakeBook(saved=True)
        _service_with(book).close_workbook_without_saving(None)
        assert book.saved_calls == 0
        assert book.closed is True

    def test_unknown_saved_flag_is_treated_as_unsaved(self):
        """Saved 를 못 읽으면 '변경 없음'이라고 단정하지 않는다."""
        book = _FakeBook(saved=None)
        _service_with(book).close_workbook_without_saving(None)
        assert book.saved_calls == 1


# ── (d) 셀 편집 모드·대화상자 ─────────────────────────────────────────────────
class TestComBusyIsNotAWriteRefusal:
    """사용 중(RPC_E_CALL_REJECTED)을 쓰기 거부로 오인하면 **사용자가 편집 중인
    통합문서를 닫는다.** 둘은 반드시 갈라야 한다."""

    BUSY = (-2147418111, -2147417846)
    REFUSAL = (-2146827284, -2146777988)

    @pytest.mark.parametrize("code", BUSY)
    def test_busy_codes_are_busy_not_refusal(self, code):
        exc = Exception(-2147352567, "예외", (0, None, None, None, 0, code))
        assert looks_like_com_busy(exc) is True
        assert looks_like_com_write_refusal(exc) is False

    @pytest.mark.parametrize("code", REFUSAL)
    def test_refusal_codes_are_refusal_not_busy(self, code):
        exc = Exception(-2147352567, "예외", (0, None, None, None, 0, code))
        assert looks_like_com_write_refusal(exc) is True
        assert looks_like_com_busy(exc) is False

    def test_plain_errors_are_neither(self):
        exc = ValueError("범위 오타")
        assert looks_like_com_busy(exc) is False
        assert looks_like_com_write_refusal(exc) is False

    def test_busy_message_tells_the_user_what_to_do(self):
        from office_claw_sidecar.services.excel_readonly_bridge import COM_BUSY_MESSAGE

        assert "Esc" in COM_BUSY_MESSAGE and "편집" in COM_BUSY_MESSAGE


# ── (d)(f) 실행 경로의 자가 복구 ──────────────────────────────────────────────
class TestDispatchRecovery:
    """`_dispatch_with_recovery` — 사용 중이면 기다렸다 다시, 연결 실패면 엔진을 다시 고른다."""

    def _busy_exc(self):
        return Exception(-2147352567, "예외", (0, None, None, None, 0, -2147418111))

    def test_busy_retries_then_succeeds(self, monkeypatch):
        from office_claw_sidecar.routers import excel_live as router

        calls = {"n": 0}

        def _dispatch(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise self._busy_exc()
            return {"ok": True}

        monkeypatch.setattr(router, "_dispatch_action", _dispatch)
        monkeypatch.setattr(router.time, "sleep", lambda s: None)
        out = router._dispatch_with_recovery(
            action="excel_live.write_range", params={}, workbook_id=None, sheet_name=None
        )
        assert out == {"ok": True}
        assert calls["n"] == 2

    def test_busy_forever_becomes_a_readable_message(self, monkeypatch):
        from office_claw_sidecar.routers import excel_live as router
        from office_claw_sidecar.services.excel_live_service import ExcelConnectionError

        monkeypatch.setattr(router, "_dispatch_action", lambda **kw: (_ for _ in ()).throw(self._busy_exc()))
        monkeypatch.setattr(router.time, "sleep", lambda s: None)
        with pytest.raises(ExcelConnectionError) as err:
            router._dispatch_with_recovery(
                action="excel_live.write_range", params={}, workbook_id=None, sheet_name=None
            )
        # 날 COM 덤프가 아니라 사람이 읽을 안내다.
        assert "Esc" in str(err.value)
        assert "2147418111" not in str(err.value)

    def test_connection_error_reselects_the_engine_once(self, monkeypatch):
        """엔진 프로브 5초 창 — Excel 을 막 열었거나 닫았을 때 한 번은 틀린 엔진으로 간다."""
        from office_claw_sidecar.routers import excel_live as router
        from office_claw_sidecar.services.excel_live_service import ExcelConnectionError

        calls = {"n": 0}
        invalidated = {"n": 0}

        def _dispatch(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ExcelConnectionError("실행 중인 Excel 인스턴스를 찾지 못했습니다.")
            return {"ok": True, "engine_retry": True}

        monkeypatch.setattr(router, "_dispatch_action", _dispatch)
        monkeypatch.setattr(
            router, "invalidate_excel_engine_cache", lambda: invalidated.__setitem__("n", invalidated["n"] + 1)
        )
        out = router._dispatch_with_recovery(
            action="excel_live.write_range", params={}, workbook_id=None, sheet_name=None
        )
        assert out["ok"] is True
        assert invalidated["n"] == 1, "엔진 캐시를 안 버렸다"
        assert calls["n"] == 2

    def test_engine_retry_does_not_loop_forever(self, monkeypatch):
        """두 번째도 연결 실패면 그대로 올린다 — 무한 재시도 금지."""
        from office_claw_sidecar.routers import excel_live as router
        from office_claw_sidecar.services.excel_live_service import ExcelConnectionError

        monkeypatch.setattr(
            router,
            "_dispatch_action",
            lambda **kw: (_ for _ in ()).throw(ExcelConnectionError("Excel 없음")),
        )
        monkeypatch.setattr(router, "invalidate_excel_engine_cache", lambda: None)
        with pytest.raises(ExcelConnectionError):
            router._dispatch_with_recovery(
                action="excel_live.write_range", params={}, workbook_id=None, sheet_name=None
            )

    def test_unrelated_errors_pass_through_untouched(self, monkeypatch):
        from office_claw_sidecar.routers import excel_live as router

        monkeypatch.setattr(
            router, "_dispatch_action", lambda **kw: (_ for _ in ()).throw(ValueError("범위 오타"))
        )
        with pytest.raises(ValueError):
            router._dispatch_with_recovery(
                action="excel_live.write_range", params={}, workbook_id=None, sheet_name=None
            )


def test_busy_retry_delays_are_short_enough_for_a_person():
    """사람이 Esc 를 누를 시간은 주되 명령이 멈춘 것처럼 보이면 안 된다."""
    from office_claw_sidecar.routers.excel_live import _COM_BUSY_RETRY_DELAYS

    assert sum(_COM_BUSY_RETRY_DELAYS) < 3.0
    assert all(d > 0 for d in _COM_BUSY_RETRY_DELAYS)
    assert time is not None  # 모듈 import 가 살아 있는지(핀의 자기 점검)
