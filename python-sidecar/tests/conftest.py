"""테스트 공통 설정."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# 테스트가 실제 logs/chat_log.jsonl을 오염시키면 사람이 읽어야 할 기록이 묻힌다.
_TEST_LOGS_DIR = Path(tempfile.gettempdir()) / "officeclaw_test_logs"
_TEST_LOGS_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("OFFICE_CLAW_LOGS_DIR", str(_TEST_LOGS_DIR))
