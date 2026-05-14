"""
analyzer.py — Phase 2 (Private-Claw) 정적 명령 분석기.

에이전트가 생성한 Python/Shell 스크립트를 Python AST + 정규식으로 분석하여
SAFE / CONFIRM / DENIED 등급을 판정한다.

등급 정의:
  DENIED  — 즉시 차단. 시스템 파괴, 권한 상승, 파이프 실행 등
  CONFIRM — 사용자 승인 필요. 파일 쓰기, 네트워크 요청 등
  SAFE    — 자동 실행 허용. 읽기 전용, 계산 등
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Literal


# ── 결과 타입 ─────────────────────────────────────────────────────────────────

@dataclass
class AnalysisResult:
    """명령 분석 결과."""

    grade: Literal["SAFE", "CONFIRM", "DENIED"]
    """판정 등급 (영어, 내부 전용)."""

    reason: str
    """한국어 사유 (사용자 표시용)."""

    matched_pattern: str = ""
    """매칭된 패턴 문자열 (로그용)."""

    code_snippet: str = ""
    """문제가 된 코드 조각 (로그용)."""


# ── 분석기 ────────────────────────────────────────────────────────────────────

class CommandAnalyzer:
    """
    에이전트가 생성한 Python/Shell 스크립트를 분석해 등급을 판정한다.

    등급: SAFE | CONFIRM | DENIED

    사용 예::

        analyzer = CommandAnalyzer()
        result = analyzer.analyze("rm -rf /tmp/test", lang="shell")
        print(result.grade)  # "DENIED"
        print(result.reason)  # "위험한 셸 명령어 감지: rm -rf"
    """

    # ── DENIED 패턴 (셸) ───────────────────────────────────────────────────────

    _DENIED_SHELL: list[tuple[str, str]] = [
        # 파일·디렉토리 파괴
        # rm -rf/-fr/-r/-f 는 DENIED, 단 word boundary로 rmarkdown/framework 등 오탐 방지
        # 경로가 시스템 루트·홈·중요 디렉토리를 포함하면 DENIED
        (r"\brm\s+-[rRfF]{1,3}\s+(?:/(?!tmp/\w)|~[/ ]|/(?:etc|usr|var|bin|sbin|lib|boot|proc|sys|dev))", "시스템 경로 강제 재귀 삭제 명령(rm -rf <system-path>)"),
        (r"\brm\s+-[rRfF]{1,3}\b", "강제 재귀 삭제 명령(rm -rf/-r/-f)"),
        (r"\brmdir\b", "디렉토리 삭제 명령(rmdir)"),
        (r"\bformat\b", "디스크 포맷 명령"),
        (r"\bmkfs\b", "파일시스템 생성 명령(mkfs)"),
        (r"\bdd\s+if=", "디스크 덮어쓰기 명령(dd)"),
        # Fork bomb
        (r":\(\)\{:\|:&\};:", "Fork bomb 패턴"),
        (r"fork\s*bomb", "Fork bomb 키워드"),
        # 권한·소유권 변경
        (r"\bchmod\s+[0-7]*7{2,3}\b", "위험한 권한 변경(chmod 777 등)"),
        (r"\bchown\b", "파일 소유권 변경(chown)"),
        # 권한 상승
        (r"\bsudo\b", "관리자 권한 명령(sudo)"),
        (r"\bsu\s+-?\s*(root|0)\b", "루트 전환 명령(su root)"),
        # 시스템 장치·설정 파일 쓰기
        (r">\s*/dev/", "/dev 장치 직접 쓰기"),
        (r">\s*/etc/", "/etc 시스템 설정 파일 쓰기"),
        (r">\s*/sys/", "/sys 커널 인터페이스 쓰기"),
        (r">\s*/proc/", "/proc 커널 프로세스 쓰기"),
        # 파이프로 셸 실행 (원격 코드 실행)
        (r"\bcurl\b[^|]*\|\s*(ba)?sh\b", "원격 스크립트 파이프 실행(curl|sh)"),
        (r"\bwget\b[^|]*\|\s*(ba)?sh\b", "원격 스크립트 파이프 실행(wget|sh)"),
        (r"\bcurl\b[^|]*\|\s*python", "원격 파이썬 스크립트 파이프 실행"),
        # 시스템 종료
        (r"\b(shutdown|reboot|halt|poweroff)\b", "시스템 종료·재시작 명령"),
        # 크론·서비스 조작
        (r"\bcrontab\s+-[er]\b", "크론 작업 수정"),
        (r"\bsystemctl\s+(enable|disable|mask|unmask)\b", "시스템 서비스 영구 설정 변경"),
    ]

    # ── DENIED 패턴 (Python AST 노드) ─────────────────────────────────────────
    # 형식: (모듈명, 함수명) — 함수명이 None이면 모듈 import 자체가 위험

    _DENIED_PYTHON_CALLS: list[tuple[str, str | None, str]] = [
        # os 모듈 위험 함수
        ("os", "remove", "파일 삭제(os.remove)"),
        ("os", "unlink", "파일 삭제(os.unlink)"),
        ("os", "rmdir", "디렉토리 삭제(os.rmdir)"),
        ("os", "system", "셸 명령 실행(os.system)"),
        ("os", "execv", "프로세스 교체 실행(os.execv)"),
        ("os", "execve", "프로세스 교체 실행(os.execve)"),
        ("os", "execvp", "프로세스 교체 실행(os.execvp)"),
        ("os", "popen", "셸 파이프 실행(os.popen)"),
        ("os", "environ", "환경변수 직접 접근(os.environ)"),
        # shutil 파괴 함수
        ("shutil", "rmtree", "디렉토리 트리 삭제(shutil.rmtree)"),
        # subprocess — 모든 형태 차단
        ("subprocess", "call", "서브프로세스 실행(subprocess.call)"),
        ("subprocess", "run", "서브프로세스 실행(subprocess.run)"),
        ("subprocess", "Popen", "서브프로세스 실행(subprocess.Popen)"),
        ("subprocess", "check_call", "서브프로세스 실행(subprocess.check_call)"),
        ("subprocess", "check_output", "서브프로세스 실행(subprocess.check_output)"),
        ("subprocess", "getoutput", "셸 출력 캡처(subprocess.getoutput)"),
        # eval / exec — 동적 코드 실행
        ("builtins", "eval", "동적 코드 실행(eval)"),
        ("builtins", "exec", "동적 코드 실행(exec)"),
        # ctypes — 네이티브 코드 실행 위험
        ("ctypes", None, "네이티브 라이브러리 접근(ctypes)"),
        # pty / tty — 터미널 탈취
        ("pty", None, "가상 터미널 접근(pty)"),
    ]

    # ── CONFIRM 패턴 (셸) ─────────────────────────────────────────────────────
    # rm 단독(플래그 없이) — 워크스페이스 파일 정리 시나리오를 위해 CONFIRM으로 격하
    # rmarkdown, framework, orm 등 무관한 단어와 구분:
    #   \brm\b 뒤에 공백+경로/파일명 형태가 오는 경우만 매칭
    #   또는 라인 끝에 rm이 단독 사용될 때도 매칭

    _CONFIRM_SHELL: list[tuple[str, str]] = [
        # 파일 삭제 (플래그 없는 rm) — CONFIRM으로 처리
        # word boundary + 공백 또는 줄 끝으로 오탐 방지
        (r"\brm\s+(?!-)", "파일 삭제 명령(rm)"),
        # find -delete 는 삭제를 수행하므로 CONFIRM
        (r"\bfind\b[^#]*-delete\b", "find -delete 파일 삭제"),
        (r"\bcp\b", "파일 복사(cp)"),
        (r"\bmv\b", "파일 이동/이름 변경(mv)"),
        (r"\btouch\b", "파일 생성(touch)"),
        (r"\bmkdir\b", "디렉토리 생성(mkdir)"),
        (r">\s*\w[\w./]+", "파일 리다이렉션 쓰기(>)"),
        (r"\bcurl\b", "네트워크 요청(curl)"),
        (r"\bwget\b", "파일 다운로드(wget)"),
        (r"\bpip\s+install\b", "패키지 설치(pip install)"),
        (r"\bapt(-get)?\s+install\b", "패키지 설치(apt install)"),
        (r"\bbrew\s+install\b", "패키지 설치(brew install)"),
        (r"\bchmod\b", "파일 권한 변경(chmod)"),
        (r"\bsystemctl\s+(start|stop|restart)\b", "시스템 서비스 제어"),
    ]

    # ── CONFIRM 패턴 (Python AST) ─────────────────────────────────────────────

    _CONFIRM_PYTHON_CALLS: list[tuple[str, str | None, str | None, str]] = [
        # 파일 열기 — 쓰기 모드만
        # open()은 AST 방문자에서 모드 인자를 체크
        ("open", None, None, "파일 쓰기(open with write mode)"),
        # pathlib 쓰기
        ("pathlib.Path", None, "write_text", "파일 쓰기(Path.write_text)"),
        ("pathlib.Path", None, "write_bytes", "파일 쓰기(Path.write_bytes)"),
        ("Path", None, "write_text", "파일 쓰기(Path.write_text)"),
        ("Path", None, "write_bytes", "파일 쓰기(Path.write_bytes)"),
        # 네트워크 요청
        ("requests", None, "get", "네트워크 GET 요청(requests.get)"),
        ("requests", None, "post", "네트워크 POST 요청(requests.post)"),
        ("requests", None, "put", "네트워크 PUT 요청(requests.put)"),
        ("requests", None, "delete", "네트워크 DELETE 요청(requests.delete)"),
        ("requests", None, "request", "네트워크 요청(requests.request)"),
        ("httpx", None, "get", "네트워크 GET 요청(httpx.get)"),
        ("httpx", None, "post", "네트워크 POST 요청(httpx.post)"),
        ("urllib", None, None, "네트워크 요청(urllib)"),
        # shutil 복사 (rmtree는 DENIED에서 처리)
        ("shutil", None, "copy", "파일 복사(shutil.copy)"),
        ("shutil", None, "copy2", "파일 복사(shutil.copy2)"),
        ("shutil", None, "move", "파일 이동(shutil.move)"),
    ]

    def __init__(self) -> None:
        # 컴파일된 패턴 캐시
        self._denied_shell_compiled = [
            (re.compile(pat, re.IGNORECASE), desc)
            for pat, desc in self._DENIED_SHELL
        ]
        self._confirm_shell_compiled = [
            (re.compile(pat, re.IGNORECASE), desc)
            for pat, desc in self._CONFIRM_SHELL
        ]
        # 화이트리스트: PermissionManager에서 로드된 안전 명령 집합
        self._whitelist: set[str] = set()

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def load_whitelist(self, commands: list[str]) -> None:
        """
        권한 설정(permissions.json)에서 로드한 화이트리스트를 적용한다.

        화이트리스트에 등록된 명령(또는 패턴)이 코드에 포함되어 있으면
        DENIED/CONFIRM 판정 이전에 SAFE로 즉시 반환한다.

        Parameters
        ----------
        commands:
            shell_command_whitelist + python_module_whitelist 항목 목록.
        """
        self._whitelist = {cmd.strip() for cmd in commands if cmd.strip()}

    def analyze(self, code: str, lang: str = "auto") -> AnalysisResult:
        """
        코드를 분석하여 AnalysisResult를 반환한다.

        화이트리스트에 등록된 명령이 코드에 포함되어 있으면 DENIED/CONFIRM 검사
        이전에 즉시 SAFE를 반환한다.

        Parameters
        ----------
        code:
            분석할 코드 문자열.
        lang:
            "python" | "shell" | "auto" (기본값 — 자동 감지)
        """
        if not code or not code.strip():
            return AnalysisResult(grade="SAFE", reason="빈 코드입니다.")

        # 화이트리스트 우선 체크 — 등록된 명령이 포함되면 즉시 SAFE
        for whitelisted in self._whitelist:
            if whitelisted and whitelisted in code:
                return AnalysisResult(
                    grade="SAFE",
                    reason=f"화이트리스트에 등록된 명령입니다: {whitelisted}",
                    matched_pattern=whitelisted,
                )

        if lang == "auto":
            lang = self._detect_language(code)

        if lang == "python":
            return self._analyze_python(code)
        else:
            return self._analyze_shell(code)

    # ── 언어 감지 ─────────────────────────────────────────────────────────────

    def _detect_language(self, code: str) -> str:
        """
        코드를 보고 언어를 추측한다.

        Python 키워드가 다수 포함되거나 AST 파싱이 성공하면 "python",
        그렇지 않으면 "shell"로 처리한다.
        """
        python_indicators = [
            r"\bimport\s+\w+",
            r"\bfrom\s+\w+\s+import\b",
            r"\bdef\s+\w+\s*\(",
            r"\bclass\s+\w+",
            r"\bprint\s*\(",
            r"\bif\s+__name__\s*==",
        ]
        score = sum(
            1
            for pat in python_indicators
            if re.search(pat, code)
        )
        if score >= 1:
            # AST 파싱 시도로 최종 확인
            try:
                ast.parse(code)
                return "python"
            except SyntaxError:
                pass

        return "shell"

    # ── Python 분석 ───────────────────────────────────────────────────────────

    def _analyze_python(self, code: str) -> AnalysisResult:
        """Python AST 분석."""
        # 먼저 셸 패턴도 체크 (python 코드 안의 셸 문자열 리터럴)
        shell_result = self._analyze_shell_in_python_strings(code)
        if shell_result.grade == "DENIED":
            return shell_result

        try:
            tree = ast.parse(code)
        except SyntaxError:
            # 파싱 실패 시 셸로 재분석
            return self._analyze_shell(code)

        visitor = _PythonASTVisitor(
            denied_calls=self._DENIED_PYTHON_CALLS,
            confirm_calls=self._CONFIRM_PYTHON_CALLS,
        )
        visitor.visit(tree)

        if visitor.denied_reason:
            return AnalysisResult(
                grade="DENIED",
                reason=f"위험한 Python 코드 감지: {visitor.denied_reason}",
                matched_pattern=visitor.denied_pattern,
                code_snippet=visitor.denied_snippet,
            )

        if visitor.confirm_reason:
            return AnalysisResult(
                grade="CONFIRM",
                reason=f"사용자 확인이 필요한 Python 코드: {visitor.confirm_reason}",
                matched_pattern=visitor.confirm_pattern,
                code_snippet=visitor.confirm_snippet,
            )

        # 셸 패턴 CONFIRM도 체크
        if shell_result.grade == "CONFIRM":
            return shell_result

        return AnalysisResult(grade="SAFE", reason="안전한 코드입니다.")

    def _analyze_shell_in_python_strings(self, code: str) -> AnalysisResult:
        """
        Python 코드 내 문자열 리터럴에 포함된 셸 명령을 검사한다.

        os.system("rm -rf /") 같은 패턴에서 문자열 내용도 분석.
        """
        # 문자열 리터럴 추출 (큰따옴표, 작은따옴표, 삼중 따옴표 포함)
        string_pattern = re.compile(
            r'""".*?"""|\'\'\'.*?\'\'\'|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'',
            re.DOTALL,
        )
        for m in string_pattern.finditer(code):
            content = m.group(0).strip("\"'")
            result = self._analyze_shell(content)
            if result.grade in ("DENIED", "CONFIRM"):
                return result
        return AnalysisResult(grade="SAFE", reason="")

    # ── Shell 분석 ────────────────────────────────────────────────────────────

    def _analyze_shell(self, code: str) -> AnalysisResult:
        """정규식 기반 셸 명령 분석."""
        # DENIED 먼저 체크
        for pattern, desc in self._denied_shell_compiled:
            m = pattern.search(code)
            if m:
                snippet = code[max(0, m.start() - 10) : m.end() + 20].strip()
                return AnalysisResult(
                    grade="DENIED",
                    reason=f"위험한 셸 명령어 감지: {desc}",
                    matched_pattern=pattern.pattern,
                    code_snippet=snippet,
                )

        # CONFIRM 체크
        for pattern, desc in self._confirm_shell_compiled:
            m = pattern.search(code)
            if m:
                snippet = code[max(0, m.start() - 10) : m.end() + 20].strip()
                return AnalysisResult(
                    grade="CONFIRM",
                    reason=f"사용자 확인이 필요한 셸 명령: {desc}",
                    matched_pattern=pattern.pattern,
                    code_snippet=snippet,
                )

        return AnalysisResult(grade="SAFE", reason="안전한 명령입니다.")


# ── Python AST 방문자 ─────────────────────────────────────────────────────────

class _PythonASTVisitor(ast.NodeVisitor):
    """
    AST를 순회하며 위험/확인 필요 패턴을 탐지한다.

    DENIED가 하나라도 발견되면 즉시 중단 플래그를 세운다.
    CONFIRM은 계속 탐색하되 DENIED가 있으면 DENIED 우선이다.
    """

    def __init__(
        self,
        denied_calls: list[tuple[str, str | None, str]],
        confirm_calls: list[tuple[str, str | None, str | None, str]],
    ) -> None:
        # DENIED: {(module, func_or_none): description}
        self._denied: dict[tuple[str, str | None], str] = {
            (mod, func): desc for mod, func, desc in denied_calls
        }
        # CONFIRM: {(prefix, method_or_none): description}
        self._confirm: dict[tuple[str, str | None], str] = {
            (prefix, method): desc
            for prefix, _, method, desc in confirm_calls
        }
        # 임포트된 모듈 별칭 추적: {"np": "numpy", "os": "os", ...}
        self._aliases: dict[str, str] = {}
        # 결과
        self.denied_reason: str = ""
        self.denied_pattern: str = ""
        self.denied_snippet: str = ""
        self.confirm_reason: str = ""
        self.confirm_pattern: str = ""
        self.confirm_snippet: str = ""
        self._stop = False

    # ── import 추적 ──────────────────────────────────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name
            self._aliases[name] = alias.name
            # ctypes / pty — 모듈 import 자체가 위험
            real = alias.name.split(".")[0]
            if (real, None) in self._denied:
                self._flag_denied(
                    self._denied[(real, None)],
                    f"import {alias.name}",
                    ast.unparse(node),
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            full = alias.asname or alias.name
            self._aliases[full] = f"{module}.{alias.name}"
        self.generic_visit(node)

    # ── Call 노드 분석 ────────────────────────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        if self._stop:
            return

        func_name, method_name = self._resolve_call(node)

        # eval / exec — 직접 이름 호출
        if func_name in ("eval", "exec"):
            self._flag_denied(
                f"동적 코드 실행({func_name})",
                func_name,
                ast.unparse(node),
            )
            return

        # DENIED 체크
        for (mod, func), desc in self._denied.items():
            if func is None:
                # 모듈 import 자체는 visit_Import에서 처리
                continue
            if self._matches(func_name, method_name, mod, func):
                self._flag_denied(desc, f"{mod}.{func}", ast.unparse(node))
                return

        # CONFIRM 체크: open() 쓰기 모드 특별 처리
        if func_name == "open":
            if self._open_is_write(node):
                self._flag_confirm(
                    "파일 쓰기(open with write mode)",
                    "open",
                    ast.unparse(node),
                )
            self.generic_visit(node)
            return

        # CONFIRM 체크
        for (prefix, method), desc in self._confirm.items():
            if prefix == "open":
                continue  # 위에서 처리
            if self._matches(func_name, method_name, prefix, method):
                self._flag_confirm(desc, f"{prefix}.{method}", ast.unparse(node))
                return

        self.generic_visit(node)

    # ── 헬퍼 ─────────────────────────────────────────────────────────────────

    def _resolve_call(self, node: ast.Call) -> tuple[str, str | None]:
        """
        Call 노드에서 (func_name, method_name) 추출.

        예:
          os.remove(x)       → ("os", "remove")
          shutil.rmtree(x)   → ("shutil", "rmtree")
          open(x)            → ("open", None)
          p.write_text(x)    → ("p", "write_text")  → 별칭 통해 (Path, write_text)
        """
        func = node.func
        if isinstance(func, ast.Attribute):
            method = func.attr
            obj = func.value
            if isinstance(obj, ast.Name):
                real = self._aliases.get(obj.id, obj.id)
                return real, method
            if isinstance(obj, ast.Attribute):
                # 예: pathlib.Path(...).write_text
                return ast.unparse(obj), method
            return ast.unparse(obj), method
        elif isinstance(func, ast.Name):
            real = self._aliases.get(func.id, func.id)
            return real, None
        return ast.unparse(func), None

    def _matches(
        self,
        func_name: str,
        method_name: str | None,
        target_mod: str,
        target_func: str | None,
    ) -> bool:
        """func_name이 target_mod이고 method_name이 target_func인지 확인."""
        if target_func is None:
            return func_name == target_mod or func_name.startswith(target_mod + ".")

        if method_name is not None:
            # obj.method 형태
            if func_name == target_mod and method_name == target_func:
                return True
            # 별칭 추적: self._aliases에 "Path" → "pathlib.Path" 등
            real = self._aliases.get(func_name, func_name)
            if real == target_mod and method_name == target_func:
                return True
        else:
            # 직접 함수 호출 형태 (예: open(...))
            if func_name == target_func:
                return True
        return False

    def _open_is_write(self, node: ast.Call) -> bool:
        """open() 호출이 쓰기 모드인지 확인한다."""
        WRITE_MODES = {"w", "wb", "a", "ab", "x", "xb", "w+", "a+", "r+", "rb+"}
        # 위치 인자 2번째가 mode
        if len(node.args) >= 2:
            mode_node = node.args[1]
            if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
                return mode_node.value in WRITE_MODES
        # 키워드 인자 mode=
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                return kw.value.value in WRITE_MODES
        # 모드 미지정 → 기본값 'r' (읽기) → SAFE
        return False

    def _flag_denied(self, reason: str, pattern: str, snippet: str) -> None:
        if not self.denied_reason:
            self.denied_reason = reason
            self.denied_pattern = pattern
            self.denied_snippet = snippet[:200]
        self._stop = True

    def _flag_confirm(self, reason: str, pattern: str, snippet: str) -> None:
        if not self.confirm_reason:
            self.confirm_reason = reason
            self.confirm_pattern = pattern
            self.confirm_snippet = snippet[:200]


# ── 싱글턴 인스턴스 ───────────────────────────────────────────────────────────

_analyzer: CommandAnalyzer | None = None


def get_analyzer() -> CommandAnalyzer:
    """전역 CommandAnalyzer 인스턴스를 반환한다 (싱글턴)."""
    global _analyzer
    if _analyzer is None:
        _analyzer = CommandAnalyzer()
    return _analyzer
