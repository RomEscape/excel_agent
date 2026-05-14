"""
messenger/base.py — Phase 3 (Private-Claw) 메신저 어댑터 추상 기반 클래스.

모든 메신저 어댑터(Telegram, Slack, Discord)는 이 ABC를 구현해야 한다.
analyze_and_guard() HITL 패턴은 각 어댑터에서 동일하게 적용된다.

공통 메시지 처리 파이프라인:
  process_message() — Slack/Discord 어댑터가 공유하는 단일 진입점.
  1. 코드 블록 감지 → analyze_and_guard()
  2. 자연어 워크스페이스 명령 → sandbox 파일 조작
  3. 그 외 → Open-CLAW (ws://127.0.0.1:18789) 또는 Ollama fallback
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# ── 공통 유틸 (코드 블록 추출) ──────────────────────────────────────────────────

def extract_code_blocks(text: str) -> str | None:
    """
    메시지에서 코드 블록 내용을 추출한다.

    - 트리플 백틱(``` ... ```) 블록 우선 추출
    - 인라인 백틱 블록은 10자 이상일 때만 추출
    - 여러 블록이면 개행으로 결합, 없으면 None 반환
    """
    triple_pattern = re.compile(r"```(?:\w+)?\n?(.*?)```", re.DOTALL)
    triple_matches = triple_pattern.findall(text)
    if triple_matches:
        combined = "\n\n".join(m.strip() for m in triple_matches if m.strip())
        return combined if combined else None

    inline_pattern = re.compile(r"`([^`]{10,})`")
    inline_matches = inline_pattern.findall(text)
    if inline_matches:
        combined = "\n".join(m.strip() for m in inline_matches if m.strip())
        return combined if combined else None

    return None


def has_code_block(text: str) -> bool:
    """텍스트에 코드 블록(트리플 백틱 또는 인라인 백틱)이 포함되어 있는지 확인한다."""
    return bool(re.search(r"```|`[^`]+`", text))


# ── 공통 메시지 처리 파이프라인 ───────────────────────────────────────────────────

# 워크스페이스 명령 패턴 (messenger/telegram.py와 동일)
_PATTERN_LIST = re.compile(
    r"(?:파일\s*목록|목록\s*보여|list\s*files?|ls\b)", re.IGNORECASE
)
_PATTERN_READ = re.compile(
    r"(?:읽어|읽기|내용\s*보여|read\b|cat\b)\s*[줘줄]?[:]?\s*(.+)?", re.IGNORECASE
)
_PATTERN_WRITE = re.compile(
    r"(?:써줘|쓰기|저장|write\b|save\b)\s*[:]?\s*(.+)?", re.IGNORECASE
)
_PATTERN_FILENAME = re.compile(
    r"['\"]([^'\"]+)['\"]"
    r"|(\S+\.\w+)"
    r"|(\.\w+)"
    r"|([A-Z][a-z]*(?:[A-Z][a-z]*)*file\b)"
)
_COMMAND_VERBS = frozenset({
    "읽어줘", "읽어", "읽기", "내용", "보여줘", "보여", "써줘", "쓰기", "저장",
    "read", "cat", "write", "save", "ls", "list", "files",
    "파일", "목록", "확인", "알려줘", "알려",
})


def _extract_filename(text: str) -> str | None:
    m = _PATTERN_FILENAME.search(text)
    if m:
        return m.group(1) or m.group(2) or m.group(3) or m.group(4)
    tokens = re.split(r"[\s,:!?]+", text.strip())
    for token in reversed(tokens):
        clean = token.strip("'\".").lower()
        if clean and clean not in _COMMAND_VERBS and len(clean) >= 2:
            return token.strip("'\".")
    return None


def _parse_write_command(text: str) -> tuple[str | None, str | None]:
    m = re.search(
        r"(\S+\.\w+)\s+(?:써줘|쓰기|저장|write|save)[:]?\s*(.*)",
        text, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m2 = re.search(
        r"(?:써줘|쓰기|저장|write|save)\s+(\S+\.\w+)[:]?\s*(.*)",
        text, re.IGNORECASE | re.DOTALL,
    )
    if m2:
        return m2.group(1).strip(), m2.group(2).strip()
    m3 = re.search(
        r"^(\S+)\s+(?:써줘|쓰기|저장|write|save)[:]?\s*(.*)",
        text.strip(), re.IGNORECASE | re.DOTALL,
    )
    if m3:
        candidate = m3.group(1).strip()
        if candidate.lower() not in _COMMAND_VERBS:
            return candidate, m3.group(2).strip()
    return None, None


async def _ws_list() -> str:
    from office_claw_sidecar import sandbox
    try:
        entries = sandbox.list_files("")
    except PermissionError:
        return "접근 거부: 워크스페이스 외부 경로입니다."
    except Exception as e:
        return f"파일 목록 조회 실패: {e}"
    if not entries:
        return "워크스페이스가 비어있습니다.\n경로: ~/PrivateClaw/Workspace"
    lines = ["워크스페이스 파일 목록:\n"]
    for entry in entries:
        prefix = "[폴더]" if entry["is_dir"] else "[파일]"
        size_str = ""
        if not entry["is_dir"]:
            kb = entry["size"] / 1024
            size_str = f" ({kb:.1f}KB)" if kb >= 1 else f" ({entry['size']}B)"
        lines.append(f"{prefix} {entry['name']}{size_str}")
    return "\n".join(lines)


async def _ws_read(filename: str) -> str:
    from office_claw_sidecar import sandbox
    if not filename:
        return "파일명을 지정해주세요. 예: test.txt 내용 읽어줘"
    try:
        content = sandbox.read_file(filename)
    except PermissionError:
        return "접근 거부: 워크스페이스 외부 경로입니다."
    except FileNotFoundError:
        return f"파일을 찾을 수 없습니다: {filename}"
    except IsADirectoryError:
        return f"{filename}은 파일이 아닌 디렉토리입니다."
    except Exception as e:
        return f"파일 읽기 실패: {e}"
    if not content.strip():
        return f"{filename}: (빈 파일)"
    truncated = content[:3000]
    suffix = "\n\n...(파일이 너무 깁니다. 앞부분만 표시)" if len(content) > 3000 else ""
    return f"{filename} 내용:\n\n{truncated}{suffix}"


async def _ws_write(filename: str, content: str) -> str:
    from office_claw_sidecar import sandbox
    if not filename:
        return "파일명을 지정해주세요. 예: notes.txt 써줘: 내용"
    if not content:
        return "저장할 내용을 입력해주세요. 예: notes.txt 써줘: 내용"
    try:
        sandbox.write_file(filename, content)
    except PermissionError:
        return "접근 거부: 워크스페이스 외부 경로입니다."
    except Exception as e:
        return f"파일 저장 실패: {e}"
    return f"{filename} 저장 완료."


async def _call_agent(text: str) -> str:
    """
    Open-CLAW WebSocket 에이전트 호출 (ws://127.0.0.1:18789).

    실패 시 Ollama 직접 호출로 fallback한다.
    """
    # Open-CLAW 시도
    try:
        import asyncio
        import json
        import websockets  # type: ignore[import]

        async with websockets.connect(
            "ws://127.0.0.1:18789", open_timeout=3, close_timeout=3
        ) as ws:
            await ws.send(json.dumps({"type": "chat", "message": text}))
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            data = json.loads(raw)
            return data.get("message") or data.get("content") or str(data)
    except Exception as e:
        logger.debug("[AgentPipeline] Open-CLAW 연결 실패, Ollama fallback: %s", e)

    # Ollama fallback
    try:
        from office_claw_sidecar.services.llm_service import get_llm_service
        llm = get_llm_service()
        return await llm.chat([{"role": "user", "content": text}])
    except Exception as e:
        logger.error("[AgentPipeline] Ollama fallback 실패: %s", e)
        return f"에이전트 처리 중 오류가 발생했습니다: {e}"


class MessengerAdapter(ABC):
    """
    메신저 어댑터 공통 인터페이스.

    Private-Claw의 모든 메신저 어댑터가 구현해야 하는 계약:
    - 시작/중지: start() / stop()
    - 텍스트 전송: send_message()
    - 파일 전송: send_file()
    - HITL 승인 요청: request_approval()

    각 어댑터는 독립적으로 시작/중지 가능하여 멀티 어댑터 동시 운영을 지원한다.

    Slack/Discord 어댑터는 process_message()를 통해 공통 파이프라인을 사용한다.
    """

    async def process_message(self, channel_id: str, user_id: str, text: str) -> str:
        """
        공통 메시지 처리 파이프라인 (Slack/Discord 어댑터용).

        처리 순서:
        1. 코드 블록 감지 → CommandAnalyzer 분석 → DENIED 차단 / CONFIRM HITL 요청
        2. 자연어 워크스페이스 명령 → 파일 목록/읽기/쓰기
        3. 그 외 → Open-CLAW 에이전트 또는 Ollama fallback

        Parameters
        ----------
        channel_id:
            응답을 보낼 채널/채팅 ID.
        user_id:
            메시지를 보낸 사용자 ID (감사 로그용).
        text:
            수신된 메시지 텍스트.

        Returns
        -------
        str
            사용자에게 전송할 응답 텍스트.
        """
        from office_claw_sidecar.analyzer import get_analyzer
        from office_claw_sidecar.command_audit import get_command_audit_logger

        # ── 1단계: 코드 블록 보안 분석 ──────────────────────────────────────────
        if has_code_block(text):
            code = extract_code_blocks(text)
            if code:
                analyzer = get_analyzer()
                cmd_audit = get_command_audit_logger()
                result = analyzer.analyze(code, lang="auto")

                if result.grade == "DENIED":
                    cmd_audit.log(
                        grade="DENIED",
                        command=code,
                        reason=result.reason,
                        lang="auto",
                        pattern=result.matched_pattern,
                        approved=False,
                        user_id=user_id,
                    )
                    return f"보안 정책 위반 — 실행 차단됨\n\n사유: {result.reason}"

                if result.grade == "CONFIRM":
                    audit_id = cmd_audit.log(
                        grade="CONFIRM",
                        command=code,
                        reason=result.reason,
                        lang="auto",
                        pattern=result.matched_pattern,
                        approved=None,
                        user_id=user_id,
                    )
                    approved = await self.request_approval(
                        channel_id=channel_id,
                        command=code,
                        reason=result.reason,
                    )
                    cmd_audit.update_approval(audit_id, approved)
                    if not approved:
                        return "작업이 취소되었습니다."
                    # CONFIRM 승인됨 → 계속 진행 (에이전트 파이프라인으로)

        # ── 2단계: 자연어 워크스페이스 명령 ─────────────────────────────────────
        if _PATTERN_LIST.search(text):
            return await _ws_list()

        if re.search(r"읽어|읽기|내용|read\b|cat\b", text, re.IGNORECASE):
            filename = _extract_filename(text)
            return await _ws_read(filename or "")

        if re.search(r"써줘|쓰기|저장|write\b|save\b", text, re.IGNORECASE):
            filename, content = _parse_write_command(text)
            if filename and content:
                # 파일 쓰기는 CONFIRM 흐름 적용
                from office_claw_sidecar.analyzer import get_analyzer
                from office_claw_sidecar.command_audit import get_command_audit_logger
                analyzer = get_analyzer()
                cmd_audit = get_command_audit_logger()
                write_code = f"# 파일 쓰기 요청\nopen('{filename}', 'w').write(content)"
                result = analyzer.analyze(write_code, lang="python")
                if result.grade in ("DENIED", "CONFIRM"):
                    audit_id = cmd_audit.log(
                        grade=result.grade,
                        command=write_code,
                        reason=result.reason,
                        lang="python",
                        pattern=result.matched_pattern,
                        approved=None,
                        user_id=user_id,
                    )
                    if result.grade == "DENIED":
                        cmd_audit.update_approval(audit_id, False)
                        return f"파일 쓰기가 차단되었습니다.\n사유: {result.reason}"
                    approved = await self.request_approval(
                        channel_id=channel_id,
                        command=write_code,
                        reason=result.reason,
                    )
                    cmd_audit.update_approval(audit_id, approved)
                    if not approved:
                        return "파일 쓰기가 취소되었습니다."
                return await _ws_write(filename, content)
            return "파일명과 내용을 모두 입력해주세요. 예: notes.txt 써줘: 내용"

        # ── 3단계: Open-CLAW 에이전트 또는 Ollama fallback ──────────────────────
        return await _call_agent(text)

    @abstractmethod
    async def start(self) -> dict:
        """
        메신저 어댑터를 시작한다.

        Returns
        -------
        dict
            {"status": "started"} 또는 {"status": "already_running"}
        """
        ...

    @abstractmethod
    async def stop(self) -> dict:
        """
        메신저 어댑터를 중지한다.

        Returns
        -------
        dict
            {"status": "stopped"} 또는 {"status": "not_running"}
        """
        ...

    @abstractmethod
    async def send_message(self, channel_id: str, text: str) -> None:
        """
        지정된 채널/대화에 텍스트 메시지를 전송한다.

        Parameters
        ----------
        channel_id:
            대상 채널/채팅 ID (메신저 플랫폼별 포맷).
        text:
            전송할 텍스트 메시지.
        """
        ...

    @abstractmethod
    async def send_file(self, channel_id: str, file_path: str) -> None:
        """
        지정된 채널/대화에 파일을 전송한다.

        Parameters
        ----------
        channel_id:
            대상 채널/채팅 ID.
        file_path:
            전송할 파일의 로컬 절대 경로.
        """
        ...

    @abstractmethod
    async def request_approval(
        self,
        channel_id: str,
        command: str,
        reason: str,
    ) -> bool:
        """
        HITL 승인 요청을 전송하고 사용자 응답을 기다린다.

        CONFIRM 등급 명령이 감지된 경우 이 메서드를 호출한다.
        메신저 플랫폼별 인터랙티브 버튼(InlineKeyboard, Block Kit, UI Button)을
        사용하여 [승인] / [거부] 버튼을 전송한다.

        Parameters
        ----------
        channel_id:
            승인 요청을 보낼 채널/채팅 ID.
        command:
            검토가 필요한 명령 (미리보기용).
        reason:
            승인이 필요한 이유 (한국어).

        Returns
        -------
        bool
            True → 승인됨, False → 거부됨 또는 타임아웃.
        """
        ...

    def is_running(self) -> bool:
        """어댑터 실행 중 여부를 반환한다. 기본 구현은 _running 속성을 확인한다."""
        return getattr(self, "_running", False)
