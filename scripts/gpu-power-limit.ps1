# GPU 전력 상한 재적용 — 부팅마다 130W를 다시 건다.
#
#   .\scripts\gpu-power-limit.ps1              # 지금 즉시 적용(관리자 필요)
#   .\scripts\gpu-power-limit.ps1 -Register    # 부팅 시 자동 적용 등록(관리자 필요)
#   .\scripts\gpu-power-limit.ps1 -Unregister
#   .\scripts\gpu-power-limit.ps1 -Check       # 현재 상한만 확인(권한 불필요)
#
# 왜(2026-08-24 라운드 0): Kernel-Power 41 하드 리셋이 재발했고, 완화책인
# `nvidia-smi -pl 130`은 **재부팅 시 165W로 복귀**한다(2026-08-10 실측).
# 리셋 한 번이 4.5시간짜리 측정(624 A/B·배터리 전수)을 통째로 날리므로,
# 이 저장소의 긴 측정 체계 전체가 이 한 줄에 기대고 있다.
# 이것은 피해 축소이지 해결이 아니다 — 전원 하드웨어(PSU) 원인은 코드 밖이다.

param(
  [switch]$Register,
  [switch]$Unregister,
  [switch]$Check,
  [int]$Watts = 130
)

$ErrorActionPreference = 'Stop'
$TaskName = 'OfficeClaw-GpuPowerLimit'
$Smi = "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
if (-not (Test-Path $Smi)) { $Smi = (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue).Source }
if (-not $Smi) { Write-Error 'nvidia-smi를 찾을 수 없습니다.'; exit 2 }

if ($Check) {
  & $Smi --query-gpu=power.limit,power.default_limit --format=csv,noheader
  return
}

if ($Unregister) {
  try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; "등록 해제: $TaskName" }
  catch { "등록된 작업이 없습니다: $TaskName" }
  return
}

if ($Register) {
  # 전력 상한 변경은 관리자 권한이 필요하므로 SYSTEM 계정 + 최고 권한으로 등록한다.
  # 등록 자체도 관리자 PowerShell에서 해야 한다 — 아니면 Access denied.
  $action = New-ScheduledTaskAction -Execute $Smi -Argument "-pl $Watts"
  $trigger = New-ScheduledTaskTrigger -AtStartup
  $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
  # CIM 오류는 Stop 선호도를 뚫고 계속 진행될 수 있다 — 실패하고도 "등록했습니다"를
  # 찍었다(2026-08-24 실측). 명시적 -ErrorAction Stop으로 성공 메시지를 지킨다.
  try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
      -Description "부팅 시 GPU 전력 상한 ${Watts}W 재적용. Kernel-Power 41 완화(2026-08-24 라운드 0)." -Force -ErrorAction Stop | Out-Null
  } catch {
    Write-Error ("등록 실패(관리자 PowerShell에서 실행해야 합니다): " + $_.Exception.Message)
    exit 1
  }
  "등록했습니다: $TaskName (부팅 시 -pl $Watts)"
  "  즉시 실행: Start-ScheduledTask -TaskName $TaskName"
  return
}

& $Smi -pl $Watts
& $Smi --query-gpu=power.limit --format=csv,noheader
