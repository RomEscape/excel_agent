# Changelog

## v3.0.0-rc1 (2026-04-16)

Private-Claw v3.0 — Zero-Trust 로컬 AI 에이전트 메신저 래퍼.
Office Claw v2.0 대비 전면 재설계.

### 핵심 변경점 (v2.0 대비)

**아키텍처 전환**
- Office Claw (오피스 업무 특화) → Private-Claw (로컬 퍼스트 보안 AI 에이전트 래퍼)
- Open-CLAW 오케스트레이션 엔진을 Secure Interceptor Wrapper 패턴으로 감쌈
- 모든 에이전트 입출력을 Tauri/Rust 보안 레이어가 통제

**Phase 1: Foundation**
- `~/PrivateClaw/Workspace` 샌드박스 격리 구현
- 텔레그램 봇 자연어 파일 조작 (목록/읽기/쓰기)
- 워크스페이스 외부 경로 접근 차단
- 온보딩 마법사 (LLM 선택 → 텔레그램 설정 → 워크스페이스 설정)

**Phase 2: Security & Guardrail**
- Python AST + 정규식 기반 정적 명령 분석기 (`analyzer.py`)
- SAFE / CONFIRM / DENIED 3단계 등급 분류
- 텔레그램 InlineKeyboard HITL 승인 흐름 (60초 타임아웃)
- 감사 로그 + 보안 대시보드 UI (SQLite 기반)
- `ApprovalDialog` 앱 내 HITL 승인 다이얼로그 연동

**Phase 3: Optimization & Scaling**
- Slack 어댑터 — Block Kit [승인]/[거부] 버튼, 에이전트 파이프라인 연결
- Discord 어댑터 — `discord.ui.View` 버튼, DM 지원, 에이전트 파이프라인 연결
- 공통 메시지 처리 파이프라인 (`MessengerAdapter.process_message()`)
  - 코드 블록 보안 분석 → 자연어 워크스페이스 명령 → Open-CLAW/Ollama
- `PermissionManager` GUI — 허용 폴더/앱/화이트리스트 관리
- 화이트리스트 → `CommandAnalyzer` 실시간 연동 (저장 즉시 + 앱 시작 시 로드)
- `MessengerSettings` UI — 텔레그램/슬랙/디스코드 연결 상태 관리
- GitHub Actions CI/CD — macOS aarch64/x86_64 + Windows x86_64 자동 빌드

### 제거된 기능 (v2.0 legacy)

- Gmail 직접 API (`/gmail`) → Open-CLAW GOG 스킬로 대체 (410 Gone)
- Excel 파싱 API (`/excel`) → Open-CLAW Excel 스킬로 대체 (410 Gone)
- Document 생성 API (`/document`) → Open-CLAW Document 스킬로 대체 (410 Gone)

### 알려진 제한 사항

- 온보딩 마법사 고도화 미완성 (v3.1 예정): Slack/Discord 설정, 권한 설정이 온보딩에 미포함
- Linux 빌드 미지원 (v3.1 예정)
- MessengerSettings 토큰 마스킹 힌트 미구현

---

## v2.0.0 (이전 버전)

Office Claw — OpenClaw 위에 한국어 UX + 보안 레이어를 얹는 방식.
Gmail/Excel/Drive 연동 특화.
