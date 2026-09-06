# 김대리

**엑셀에 말로 일을 시키는 업무 비서입니다.** "합계를 표 아래에 넣어줘"처럼 평소 말투로 적으면 내 PC의 Excel에서 바로 처리합니다.
파일도 AI도 내 컴퓨터 밖으로 나가지 않습니다. 내용을 바꾸는 작업은 실행 전에 확인을 물어봅니다.

| 하고 싶은 것 | 보는 곳 |
|---|---|
| Windows에 설치하고 싶다 | **[Windows 설치 안내](https://claude.ai/code/artifact/e4354599-27b5-414c-8723-65d29922b9c1)** (단계별, 복사 버튼 포함) |
| Mac에 설치하고 싶다 | **[Mac 설치 안내](https://claude.ai/code/artifact/89905586-97dd-41eb-9383-c7f219fff0a3)** (Apple Silicon, 실기 검증 전) |
| 쓰는 법이 궁금하다 | [처음 켰을 때](#처음-켰을-때) · [이렇게 시키면 됩니다](#이렇게-시키면-됩니다) |
| 뭔가 안 된다 | [안 될 때](#안-될-때) |
| 개발자다 | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |

---

## 이런 일을 시킬 수 있습니다

- **표 정리**: 합계·평균 넣기, 정렬, 중복 정리, 조건에 맞는 행만 남기기
- **꾸미기**: 머리글 색과 굵기, 테두리, 숫자에 콤마, 조건에 맞는 칸만 색칠
- **차트와 시트**: 선그래프·막대·도넛 차트 만들기, 시트 추가·이름 바꾸기
- **값 채우기**: Excel에서 넣을 자리를 복사해 채팅창에 붙이고 값을 이어 적으면 그 자리에 씁니다
- **수식**: "E열에 A에서 C 뺀 값" 같은 말이나 `=AVERAGE(D2:D6)` 같은 수식 그대로

## 준비물

- Windows 10/11 (64비트) 또는 Apple Silicon Mac
- 데스크톱 Excel (Microsoft 365 또는 2016 이상)
- 디스크 여유 15GB와 인터넷 (AI 모델 약 9GB를 한 번 내려받습니다)
- 저장소 접근 권한 (비공개 저장소라 담당자가 GitHub 계정에 권한을 줘야 합니다)

## 설치하기

자세한 순서는 위 설치 안내 링크에 있습니다. 요약하면 명령 세 줄이 전부입니다. 약 20~40분 걸리고, 대부분은 내려받는 시간입니다.

**Windows** — PowerShell에서:

```powershell
git clone -b openclaw_jinh_demo https://github.com/sadStoneTurtle/officeclaw.git
cd officeclaw
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

**Mac** — 터미널에서 (Xcode 개발 도구와 Homebrew가 먼저 있어야 합니다. 안내 1단계 참고):

```bash
git clone -b openclaw_jinh_demo https://github.com/sadStoneTurtle/officeclaw.git
cd officeclaw
./scripts/setup.sh
```

설치가 끝나면 **창을 새로 열고** 켭니다.

```
cd officeclaw
npm run tauri:dev
```

처음에는 조립하느라 2~3분 걸리고, 그다음부터는 금방 뜹니다. 클릭 한 번으로 끝나는 설치 파일은 준비 중입니다.

## 처음 켰을 때

"김대리 시작하기" 안내가 세 단계로 뜹니다.

1. **파일 설치** — 자동으로 확인됩니다.
2. **AI 엔진 준비** — 목록에서 **skt/A.X-4.0-Light**가 기본으로 골라져 있습니다. 그대로 확인을 누릅니다. 엑셀 계획용 모델은 함께 준비됩니다.
3. **워크스페이스 지정** — 폴더 경로가 자동으로 채워집니다. 잠깐 비어 있으면 채워질 때까지 기다렸다가 확인을 누릅니다.

Mac은 첫 엑셀 명령에서 "Excel을 제어하려고 합니다" 창이 뜹니다. **허용**해야 합니다.

**엑셀 파일은 `officeclaw` 안의 `엑셀 작업 폴더`에 둡니다.** 김대리는 이 폴더 안의 파일만 다룹니다. 연습용으로 `AI_Excel_Automation_Demo.xlsx`가 들어 있습니다.

- 파일 목록에서 **한 번 클릭** = 그 파일을 작업 대상으로 고릅니다("대상" 표시가 붙습니다).
- **더블클릭** = Excel로 엽니다. Excel에서 열어 둔 채로 시키면 그 파일에 바로 반영됩니다.

## 이렇게 시키면 됩니다

셀 주소를 몰라도 됩니다. 아래는 실제로 통과한 문장들입니다.

- `합계를 표 아래에 한 줄로 넣어줘` / `밑에 합계 한줄 부탁해`
- `첫줄 남색 배경으로 하고 글자는 흰색 굵게` / `표 전체 테두리좀 둘러줘`
- `클레임 10 넘는 데만 빨갛게 칠해줘` / `상태가 대기인 애들만 분홍으로`
- `주문건수 많은 순으로 정렬해줘` / `수도권 행만 남기고 나머지는 치워줘`
- `정시배송률 가지고 선그래프 하나 뽑아줘` / `요약이라는 이름으로 시트 추가좀`

**표 채우기**: Excel에서 넣을 자리를 드래그해 복사(Ctrl+C / ⌘C), 채팅창에 붙여넣기(Ctrl+V / ⌘V) 한 뒤 값을 이어 적습니다. 쉼표가 칸, 세미콜론이 줄입니다.

```
지역,주문건수,출고건수; 수도권,10452,10120; 충청권,3892,3773 입력해줘
```

## 안 될 때

| 이런 화면이 보이면 | 이렇게 하세요 |
|---|---|
| `cargo … program not found` / `cargo: command not found` | 설치 전에 열어 둔 창입니다. 창을 새로 열고 다시 켭니다. 편집기 안 터미널이면 편집기를 재시작합니다. |
| `os error 5` 로 실행 실패 | 김대리가 이미 켜져 있습니다. 창을 닫고 다시 켭니다. |
| "스크립트 실행이 정책 때문에…" (Windows) | 설치 명령을 `-ExecutionPolicy Bypass`까지 포함해 그대로 붙여넣습니다. |
| 채팅은 되는데 엑셀 작업이 엉망 | AI 모델이 없습니다. 설치 명령을 다시 실행하면 모델만 받고 끝납니다. |
| 명령마다 20~30초 걸리다 "…초 안에 답하지 못했습니다" | 그래픽카드가 없거나 작은 PC입니다. Windows는 `setx EXCEL_LIVE_PARSE_TIMEOUT_SECONDS 45`, Mac은 `~/.zshrc`에 `export EXCEL_LIVE_PARSE_TIMEOUT_SECONDS=45`를 넣고 껐다 켭니다. |
| 파일을 더블클릭해도 Excel이 안 뜸 | 2026-09-06 이후 버전에서 고쳤습니다. `officeclaw` 폴더에서 `git pull` 후 다시 켭니다. |
| Mac에서 엑셀 작업이 전부 실패 | 자동화 권한을 거부한 것입니다. 시스템 설정 → 개인정보 보호 및 보안 → 자동화에서 김대리 아래 Excel을 켭니다. |

더 자세한 원인과 개발 관련 내용은 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)에 있습니다.

---

- Windows 설치 안내: https://claude.ai/code/artifact/e4354599-27b5-414c-8723-65d29922b9c1
- Mac 설치 안내: https://claude.ai/code/artifact/89905586-97dd-41eb-9383-c7f219fff0a3
- 개발자 안내: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) · 작업 규율: [CLAUDE.md](CLAUDE.md) · 실측 기록: [개발일지.md](개발일지.md)
- Windows 명령은 2026-09-06 새 Windows 11 PC에서 실제로 실행해 확인했습니다. Mac은 설치 스크립트 검토와 드라이런까지만 했고 실제 Mac에서는 아직 돌려 보지 않았습니다.
