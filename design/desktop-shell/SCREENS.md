# 화면 스펙 — 최종 채택안 (14화면 × 라이트/다크)

Figma 파일: `김대리_기획안` (`ceXnKBY4lTOvhKTEhNQlfe`)
링크는 해당 프레임으로 바로 열린다.

노드 트리 원본은 [`raw/light/`](raw/light) · [`raw/dark/`](raw/dark) 에 있다.
(Figma REST API로 추출 — 텍스트·크기·fill 포함)

---

## A. 온보딩 / 설치 (7화면) — 구버전 와이어프레임에 **없던 영역**

3단계 스텝 인디케이터가 모든 화면 하단에 공통으로 붙는다: `파일 설치` → `모델 설치` → `워크스페이스 지정`.
활성 스텝은 `#56C331` 채움, 비활성은 흰 배경 + 초록 숫자.

### A-1. 파일 설치 진행 — [Frame 159](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-78) / 다크 [145](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-137)

- 타이틀 `김대리 설치를 시작합니다.` + 로고 36×36
- 진행 바 467×12 — 채움 25/467 (≈5%)
- 진행 라벨 좌: `현재 파일명 abcdefg..` / 우: `1734/10200(17%)`
- 파일 체크리스트 (계단식 배치, 카드 3장):
  `Pakage.json` ✅ / `README.md` ✅ / `requirements.txt` ⏳(빈 원)
- 스텝 1 활성

### A-2. 파일 설치 완료 — [Frame 160](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-196) / 다크 [146](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-255)

- A-1과 동일 레이아웃, 진행 바 100% 채움
- 라벨 `설치 완료` / `10200/10200(100%)`
- 스텝 1 여전히 활성 (다음 단계로 넘어가기 직전 상태)

### A-3. 모델 선택 — 드롭다운 닫힘 — [Frame 161](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-329) / 다크 [147](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-383)

- 타이틀 `설치할 AI 모델을 선택해주세요.`
- 셀렉트 441×40: Google 아이콘 + `Gemma 4.0` + `추천` 배지 + chevron-down
- CTA `확인` 120×36 `#2DB400`
- 헬프 텍스트 `AI 모델은 추후에 언제든 변경이 가능합니다.`
- 스텝 2 활성

### A-4. 모델 선택 — 드롭다운 열림 — [Frame 162](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-437) / 다크 [148](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-536)

- 셀렉트 아래 옵션 리스트 431×132, 각 항목 407×20 + `추천` 배지:

| 모델 | 아이콘 |
|---|---|
| `Opus 4.0` | Claude (`#FF7043`) |
| `Deepseek` | Deepseek (`#4D6BFE`) |
| `Gemma 3.0` | Google 4색 |
| `KIMI` | Kimi (`#092400`) |

> ⚠️ 4개 항목 **전부** `추천` 배지가 달려 있다. 배지가 모든 항목에 붙으면 변별력이 없다.
> 디자이너 의도 확인 필요 — 컴포넌트 복제 흔적일 가능성.

### A-5. 모델 선택 — 설치 완료 + 드롭다운 열림 — [Frame 163](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-635) / 다크 [149](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-734)

- A-4와 동일 + 상단 진행 바가 `설치 완료` / `10200/10200(100%)` 상태

### A-6. 워크스페이스 지정 — 미선택 — [Frame 164](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-833) / 다크 [150](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-874)

- 타이틀 `워크스페이스 위치를 지정해주세요.`
- 폴더 선택 필드 441×40: 폴더 아이콘 + placeholder `폴더를 선택해주세요.`
- **CTA `확인` 비활성** — 배경 `#E1E6DF`
- 헬프 텍스트 `워크스페이스 위치는 추후에 언제든 변경이 가능합니다.`
- 스텝 3 활성

### A-7. 워크스페이스 지정 — 선택됨 — [Frame 165](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-915) / 다크 [151](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-956)

- 필드 값 `바탕화면`
- **CTA `확인` 활성** — `#2DB400`

---

## B. 앱 셸 (7화면)

### 공통 — 좌측 사이드바 240px

와이어프레임의 사이드바 구성 (위→아래):

1. 로고 (`아트보드 5 2` 126×36) + `layout-sidebar-left-collapse` 접기 아이콘
2. 내비 4항목 (각 240×44):
   - `대시보드` (layout-dashboard)
   - `워크스페이스` (subtitles-edit)
   - `작업 겁색` (input-search) + **chevron-down** ← 오타. `작업 검색`
   - `대화목록` (message-circle) + **chevron-down**
3. 하단 푸터 (각 216×44): `도움말` (help) · `환경 설정` (settings)

> 라이트 모드에서 사이드바 지면은 `#FDFEFC` — 본문과 같은 밝기다.

### B-1. 홈 / 워크스페이스 — [Frame 166](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-997) / 다크 [152](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-1190)

1600×**1231** (다른 화면보다 세로가 길다 — 스크롤 지면)

- 배경 장식 타원 938×632 `#7FD163`
- 헤드라인 `좋은 아침입니다, 무엇을 도와드릴까요?` (530×50) + 로고
- 서브 `당신의 업무비서 김대리 대기중입니다`
- **문서 액션 바** 751×44 — `새로운 문서 생성` / `문서 업로드` / `문서 삭제`
- **문서 카드 그리드** — 카드 242×268, 각 카드 = 썸네일 180×239 + 하단 메타 바(엑셀 아이콘 + `마케팅 시장조사` + `1일 전`/`3일 전`/`4일 전`/`5일 전`)
  - 현재 6장 배치 + `15개 문서 더보기...` 타일 (플러스 아이콘)
- 하단 컴포저 750×48 — plus 버튼 + placeholder `김비서에게 명령을 내려주세요.`
- 상태 표시 `로컬 에이전트 작동중` + `#2DB400` 점 8×8

> **구버전과 가장 크게 갈리는 화면.** 구버전 1번 화면은 상태 카드 3장(보안 상태 / 최근 분석 데이터 / 예정된 자동화) + `새 업무 시작하기` CTA 였다.
> 신규안은 그 자리가 통째로 **문서 카드 그리드 + 문서 CRUD 액션 바**로 바뀌었다.

### B-2. 채팅 패널 — 플로팅 — [Frame 169](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-1857) / 다크 [155](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-1984)

- 본문 영역 `Frame 120` 1356×900 (엑셀/문서 지면)
- 채팅 패널 **390×507** — 지면 위에 떠 있는 카드
- 패널 상단: `tabler:aspect-ratio` **크기 조절 아이콘** + 로고 + 워드마크
- 빠른 프롬프트 칩 3개 (358×32): `중복 데이터 처리해줘` / `배송 형태로 필터링해줘` / `배송 형태로 필터링해줘` + chevron-right 더보기
- 컴포저 390×143: placeholder `김비서에게 명령을 내려주세요.` + paperclip(첨부) + arrow-up(전송)

### B-3. 채팅 패널 — 전체 높이 — [Frame 168](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-1585) / 다크 [154](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-1721)

- 채팅 패널 **390×900** (전체 높이 도킹), 본문 영역 962×900으로 축소
- 나머지는 B-2와 동일 — 즉 **같은 패널의 크기 변형**

> B-2 / B-3 / B-6~B-8이 전부 같은 패널의 다른 상태다.
> `aspect-ratio` 아이콘 = **채팅 패널 크기 토글(플로팅 ↔ 도킹)**. 구버전에는 없던 상호작용.

### B-4. QR 페어링 — [Frame 167](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-1383) / 다크 [153](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-1484)

- QR 코드 160×160
- 페어링 코드 필드 334×44: `akjsdbnjjknjjc98us89200cnj023ldsl` + copy 아이콘
- **TTL 카운트다운** `입력 가능 시간` `3:29` + 재발급(retry) 아이콘
- 안내 `아직 김대리 앱이 없으신가요? / 김대리를 다운받고 더 많은 기능을 사용해보세요.`
- 스토어 배지 2개: Google Play (153×48) · App Store (139×48)

### B-5. 채팅 — 1턴 — [Frame 170](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-2111) / 다크 [156](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-2251)

- 사용자 말풍선 `#2DB400` / AI 말풍선 `#E1E6DF`
- 사용자: `아시아 국가의 주문 현황을 조회해줘`
- AI: `아시아 국가의 주문 현황은 동아시아 2,098.89, 동남아시아 1,897.32, 중앙아시아 1,329.09 입니다.`
- **말풍선 하단 액션 행** — 사용자: `arrow-back`(되돌리기) · `pencil`(편집) · `copy` / AI: `arrow-back` · `copy`
- **타임스탬프** `오후 8:00` 우측 정렬

### B-6. 채팅 — 승인(CONFIRM) 프롬프트 — [Frame 171](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-2390) / 다크 [157](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-2535)

- 사용자: `아시아 국가의 주문 현황만 따로 분리하여 시트를 생성해주세요.`
- AI: `아시아 국가의 주문 형황을 분리한 459개의 행을 가진 새로운 시트를 제작하고자합니다. 진행할까요?`
- **인라인 승인 버튼 2개** (각 56×21): `네 Y` / `아니오 N`
- 액션 행과 같은 줄에 배치됨

> 기존 엑셀 CONFIRM 승인 체인에 직접 대응. 현재는 `ApprovalDialog`(모달)로 구현돼 있다 — 신규안은 **말풍선 인라인**이다.

### B-7. 채팅 — 전체 실행 흐름 — [Frame 172](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-2680) / 다크 [158](https://www.figma.com/design/ceXnKBY4lTOvhKTEhNQlfe/?node-id=107-2871)

가장 정보가 많은 화면. 스레드 358×683 안에 아래 순서로 쌓인다:

1. 사용자 요청 말풍선 ×2
2. **스켈레톤 로딩** — `새로운 시트 제작중...` + 스피너 원 14×14 + shimmer 바 (`Component 1` 358×48, 내부 `Rectangle 28` 256×48)
3. 사용자 응답 말풍선 `네 Y` (41×28) — 승인이 대화 기록에 남는다
4. AI CONFIRM 말풍선 (`진행할까요?`)
5. **툴 진행 스텝 칩** 358×28 `#E1E6DF`: `문서 형식 파악 완료` → `데이터 처리 완료`
6. AI CONFIRM 말풍선 재노출

> **신규 요소 3개**: 스켈레톤 로딩 / 툴 진행 스텝 칩 / 승인이 메시지로 남는 패턴.
> 구버전 5번 화면(막대 대시보드)과 4번(스프레드시트 미리보기)에 해당하는 **인라인 결과 카드는 신규안 프레임에 없다.**
> 이미 구현된 `SheetPreviewCard` · `BarChartCard`를 어떻게 할지 결정 필요 (아래 README 참고).

---

## 텍스트 오탈자 (디자이너 확인 필요)

와이어프레임에 그대로 들어있는 오타다. 코드에 복사하기 전에 고쳐야 한다.

| 위치 | 현재 | 수정 |
|---|---|---|
| 사이드바 내비 | `작업 겁색` | `작업 검색` |
| 설치 화면 파일 목록 | `Pakage.json` | `Package.json` |
| B-6 / B-7 AI 말풍선 | `주문 형황` | `주문 현황` |
| B-7 AI 말풍선 | `제작하고자합니다` | `제작하고자 합니다` |
| 컴포저 placeholder | `김비서에게 명령을 내려주세요.` | 앱 이름은 `김대리` — `김비서`가 맞는지 확인 |
