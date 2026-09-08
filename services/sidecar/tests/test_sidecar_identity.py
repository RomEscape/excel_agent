# -*- coding: utf-8 -*-
"""낡은 사이드카 감지 — 기동 시각과 소스 수정 시각의 대소만 본다.

2026-09-08 실측: 워크스페이스 경로를 바꾼 커밋 이전에 뜬 사이드카가 살아남아
앱이 옛 폴더를 보고 "파일을 찾을 수 없습니다"를 반복했다. 그때 `/health` 는
`status:"ok"` 만 냈고 앱은 초록불이었다.
"""

import office_claw_sidecar.sidecar_identity as identity


def test_방금_뜬_프로세스는_낡지_않았다():
    assert identity.describe_running_sidecar()["code_stale"] is False


def test_소스가_기동_뒤에_바뀌었으면_낡았다(monkeypatch):
    # 소스 수정 시각보다 **앞서** 뜬 프로세스 = 그 변경 이전 코드를 들고 도는 중.
    monkeypatch.setattr(identity, "_STARTED_AT", identity.newest_source_mtime() - 60)
    assert identity.describe_running_sidecar()["code_stale"] is True


def test_여유_안쪽의_차이는_낡음이_아니다(monkeypatch):
    # 기동 도중 저장이 몇 초 늦게 반영되는 경우까지 낡음으로 부르면 오탐이 된다.
    newest = identity.newest_source_mtime()
    monkeypatch.setattr(identity, "_STARTED_AT", newest - (identity._STALE_MARGIN_S - 1))
    assert identity.describe_running_sidecar()["code_stale"] is False


def test_배포본은_판정_대상이_아니다(monkeypatch):
    # PyInstaller 배포본은 소스 트리가 없다 — 검사할 대상 자체가 없다.
    monkeypatch.setattr(identity.sys, "frozen", True, raising=False)
    monkeypatch.setattr(identity, "_STARTED_AT", 0.0)
    got = identity.describe_running_sidecar()
    assert got["frozen"] is True
    assert got["code_stale"] is False


def test_신원에_앱이_대조할_것들이_다_있다():
    got = identity.describe_running_sidecar()
    for key in ("pid", "started_at", "newest_source_mtime", "frozen", "code_stale", "workspace_dir"):
        assert key in got
    # 워크스페이스는 판정에 쓰지 않지만, 어느 폴더를 보고 있었는지는 보여야 한다.
    assert got["workspace_dir"]


def test_health_응답에_신원_블록이_실린다():
    import asyncio

    from office_claw_sidecar.routers.health import health_check

    body = asyncio.run(health_check())
    assert "sidecar" in body
    assert body["sidecar"]["pid"] > 0
