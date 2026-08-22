# 야간 게이트 실행기 — Windows 작업 스케줄러가 부르는 얇은 껍데기.
#
#   .\scripts\nightly-gates.ps1              # 전부 (pytest + 파괴 72 + 말투 624, 약 70분)
#   .\scripts\nightly-gates.ps1 -Only guard  # 하나만
#   .\scripts\nightly-gates.ps1 -Register    # 매일 밤 03:00 자동 실행 등록
#   .\scripts\nightly-gates.ps1 -Unregister  # 등록 해제
#
# 결과: logs\nightly\LATEST.md (기준선보다 나빠지면 맨 위에 ❌와 항목이 뜬다)
# 종료코드: 0 유지 · 1 나빠짐 · 2 실행 불가(자물쇠·인터프리터 없음)

param(
  [ValidateSet('pytest', 'guard', 'blind')][string[]]$Only,
  [switch]$UpdateBaseline,
  [switch]$Register,
  [switch]$Unregister,
  [string]$At = '03:00'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$TaskName = 'OfficeClaw-NightlyGates'

if ($Unregister) {
  try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; "등록을 해제했습니다: $TaskName" }
  catch { "등록된 작업이 없습니다: $TaskName" }
  return
}

if ($Register) {
  # 로그온 여부와 무관하게 돌면 좋겠지만, 그건 저장된 자격증명을 요구한다.
  # 이 개발기는 늘 로그온 상태이므로 대화형 사용자로 등록한다(관리자 권한 불필요).
  $ps = (Get-Command powershell.exe).Source
  $action = New-ScheduledTaskAction -Execute $ps `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSCommandPath`"" `
    -WorkingDirectory $Root
  $trigger = New-ScheduledTaskTrigger -Daily -At $At
  # 배터리로 돌 때도 실행한다 — 이 개발기는 데스크톱이고, 안 돌면 기준선이 늙는다.
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description '야간 게이트: pytest + 파괴 게이트 72 + 말투 게이트 624. 결과는 logs\nightly\LATEST.md' -Force | Out-Null
  "등록했습니다: $TaskName (매일 $At)"
  "  해제: .\scripts\nightly-gates.ps1 -Unregister"
  "  즉시 실행: Start-ScheduledTask -TaskName $TaskName"
  return
}

$py = $env:OFFICECLAW_PY
if (-not $py) { $py = Join-Path $env:LOCALAPPDATA 'officeclaw\venvs\python-sidecar\Scripts\python.exe' }
if (-not (Test-Path $py)) { Write-Error "파이썬을 찾을 수 없습니다: $py"; exit 2 }

$env:PYTHONUTF8 = '1'
$argsList = @((Join-Path $Root 'scripts\nightly_gates.py'))
foreach ($o in $Only) { $argsList += @('--only', $o) }
if ($UpdateBaseline) { $argsList += '--update-baseline' }

& $py @argsList
exit $LASTEXITCODE
