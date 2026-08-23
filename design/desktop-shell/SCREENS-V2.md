# 화면 스펙 — 개선안 (대시보드 폐기 → 작업 기록)

Figma 파일: `김대리_기획안` (`ceXnKBY4lTOvhKTEhNQlfe`)
노드 트리 원본은 [`raw/v2/`](raw/v2) 에 있다 (Figma REST API 추출 — 텍스트·크기·fill 포함).

[`SCREENS.md`](SCREENS.md)의 14화면 다음에 그려진 **개선안 10프레임**이다.
Figma에서는 10개 모두 `D3 · 대시보드 — 첫 실행 / 빈 상태 (1600x900)`이라는 **같은 이름**을 달고 있는데,
복제 후 프레임 이름을 고치지 않은 흔적이고 실제 내용은 서로 다른 3종류다. 아래 표가 실제 대응이다.

> **다크 짝이 없다.** 14화면과 달리 이 10프레임은 라이트 전용이다.
> 다크 대응값이 `TOKENS.md`에 없는 신규 요소(상태 배지·표 구분선·검색 입력창)는
> `lib/activityLog.js`가 `dark:` 변형으로 들고 있다. 다크 프레임이 그려지면 그 값으로 맞출 것.

| 화면 | node-id | 링크 |
|---|---|---|
| 작업 기록 — 기본 | `229:4056` | [열기](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=229-4056) |
| 작업 기록 — 초기판 | `229:2807` | [열기](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=229-2807) |
| 작업 기록 — 빈 상태 | `229:4527` | [열기](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=229-4527) |
| 대화목록 — 유형별 | `229:3237` | [열기](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=229-3237) |
| 대화목록 — 파일별 | `229:3678` | [열기](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=229-3678) |
| 환경 설정 — 기본 | `243:1140` | [열기](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=243-1140) |
| 환경 설정 — 기기 없음 | `243:1370` | [열기](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=243-1370) |
| 환경 설정 — QR 페어링 모달 | `244:1599` | [열기](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=244-1599) |
| 환경 설정 — 연결 성공 모달 | `250:6498` | [열기](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=250-6498) |
| 환경 설정 — 연결 해제 확인 | `250:6764` | [열기](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=250-6764) |

---

## 사이드바 (240px)

14화면판에서 **두 항목이 바뀌었다.**

| 개선안 | 14화면판 | 아이콘 (tabler → lucide) |
|---|---|---|
| 워크스페이스 | 워크스페이스 | `subtitles-edit` → `SquarePen` |
| **작업 기록** | ~~대시보드~~ + ~~작업 검색~~ | `input-search` → `TextSearch` |
| 대화목록 | 대화목록 | `message-circle` → `MessageCircle` |
| **파일 목록** *(신설)* | — | `file` → `FileText` |
| 도움말 · 환경 설정 (푸터) | 동일 | `help` · `settings` |

- 활성 항목 배경 `#ECF8E8`
- `파일 목록`은 chevron 확장 — 안에 워크스페이스 문서가 `arrow-back` 아이콘 + 파일명 + `1시간 전` 형태로 붙는다 (216×36)

## 작업 기록 (`activity`)

본문 지면 `#F6F7F5`, 카드 `#FDFEFC`, 콘텐츠 폭 1280.

**작업 요약** — KPI 4장 (306×132)

| 라벨 | 보조 문구 | 출처 |
|---|---|---|
| 전체 명령 | 누적 처리 건수 | `stats.total` |
| 완료된 명령 | 자동 실행 + 승인 | `stats.safe + stats.confirm_approved` |
| 차단된 명령 | 보안 정책 위반 | `stats.denied + stats.confirm_rejected` |
| 자동 마스킹 | 민감정보는 AI에 전달되기 전에 자동 마스킹됩니다. | `securityStats.masking.total` |

> 14화면판의 `승인 대기` 카드가 빠지고 그 자리에 `자동 마스킹`이 올라왔다.
> 보안 카드는 통째로 없앴다 — 카드 안의 `차단된 명령`이 KPI와 중복이었다.

**최근 활동** — 검색 + 표 + 페이지네이션

- 검색 입력 358×38, placeholder `검색어를 입력해주세요.`, 우측 `tabler:search`
- 표 헤더 4칸, 각 칸에 정렬 chevron (`#6B7468`): `디바이스`(104) · `명령`(976) · `상태`(100) · `시간`(100)
- 행 높이 54. `명령` 칸만 2줄 — 명령문 + 대상 파일명
- 상태 배지 80×32 — `완료` `#ECF8E8` / `차단` `#F8D1C9`
- 시간 — 오늘이면 `오후 08:00`, 그 이전이면 `6일 전` · `2주일 전` · `1달 전`
- 빈 상태 — `아직 활동 기록이 없습니다` + `김대리에게 첫 명령을 내리면 여기에 모든 작업이 기록됩니다.`
- 페이지네이션 344×80 — `‹ 1 2 3 4 5 … 30 ›`, gap은 점 3개 `#CACFC7`

## 대화목록 · 환경 설정

`대화목록`(유형별/파일별 탭)과 `환경 설정`(기기 연결·QR 페어링·모달 3종)은
이번 PR 범위에 넣지 않았다. QR 페어링 카운트다운·스토어 배지는 이미
[`RelayPairing.jsx`](../../apps/desktop/src/components/relay/RelayPairing.jsx)와
`lib/pairingCountdown.js`가 구현하고 있어 프레임과 대체로 일치한다.
