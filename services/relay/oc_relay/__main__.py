"""릴레이 서버 기동 진입점.

    python -m oc_relay          # 또는 설치된 `oc-relay` 스크립트
    PORT=9000 python -m oc_relay

데스크톱/모바일이 모두 아웃바운드로 접속하므로 relay는 공개 인터페이스에 바인딩한다.
프로덕션은 앞단에 TLS 종단(reverse proxy) + 443 WSS 를 둔다.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("PORT", "8787"))
    uvicorn.run("oc_relay.app:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
