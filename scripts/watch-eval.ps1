# 진행 중인 작업의 남은 시간을 보여 준다.
#
#   .\scripts\watch-eval.ps1 -Total 308        # Ollama 작업 (평가·진단·앱 명령)
#   .\scripts\watch-eval.ps1 -Workflow         # Claude 서브에이전트 워크플로
#   .\scripts\watch-eval.ps1 -Total 154 -Once  # 한 번만
#
# 중요: 두 종류의 작업이 있고 세는 곳이 다르다.
#   - Ollama 작업(플래너 평가, 사이드카 명령, 진단) -> Ollama 서버 로그를 센다
#   - Claude 워크플로(서브에이전트 fan-out)        -> Ollama를 **전혀 쓰지 않는다.**
#     이때 -Total 로 Ollama를 보면 0건이 잡히고 남은 시간이 엉터리로 나온다. -Workflow 를 써라.
#
# 속도는 "최근 10건"으로 잰다. 전체 평균을 쓰면 앞선 작업이 끝난 뒤의 유휴 시간까지
# 건당 시간에 섞여 들어가 남은 시간이 터무니없이 부풀려진다.

[CmdletBinding()]
param(
    [int]$Total = 0,
    [switch]$Workflow,
    [string]$Since = "",
    [int]$IntervalSeconds = 10,
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Show-WorkflowProgress {
    $base = Join-Path $env:USERPROFILE ".claude\projects"
    $dirs = Get-ChildItem $base -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem $_.FullName -Directory -ErrorAction SilentlyContinue } |
        ForEach-Object { Join-Path $_.FullName "subagents\workflows" } |
        Where-Object { Test-Path $_ } |
        ForEach-Object { Get-ChildItem $_ -Directory -ErrorAction SilentlyContinue }
    if (-not $dirs) { Write-Host "실행 중인 워크플로가 없습니다." -ForegroundColor Yellow; return $true }

    $run = $dirs | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $journal = Join-Path $run.FullName "journal.jsonl"
    if (-not (Test-Path $journal)) { Write-Host "journal 없음: $($run.Name)" -ForegroundColor Yellow; return $true }

    $lines = Get-Content $journal -ErrorAction SilentlyContinue
    $started = @($lines | Where-Object { $_ -match '"type":"started"' }).Count
    $done = @($lines | Where-Object { $_ -match '"type":"result"' }).Count
    $idle = (New-TimeSpan -Start $run.LastWriteTime -End (Get-Date)).TotalSeconds
    $pct = if ($started -gt 0) { $done * 100 / $started } else { 0 }
    $bar = "#" * [int]($pct / 4) + "." * (25 - [int]($pct / 4))

    Write-Host ("[{0}] {1,3:N0}%  에이전트 {2}/{3} 완료  마지막 갱신 {4:N0}초 전  ({5})" -f
        $bar, $pct, $done, $started, $idle, $run.Name)
    return ($idle -gt 120)
}

function Get-RequestTimes {
    $log = "$env:LOCALAPPDATA\Ollama\server.log"
    if (-not (Test-Path $log)) { return @() }
    Select-String -Path $log -Pattern 'POST +"/v1/chat/completions"' | ForEach-Object {
        if ($_.Line -match '(\d{4})/(\d{2})/(\d{2}) - (\d{2}):(\d{2}):(\d{2})') {
            [datetime]("$($matches[1])-$($matches[2])-$($matches[3]) $($matches[4]):$($matches[5]):$($matches[6])")
        }
    }
}

function Show-OllamaProgress {
    param([datetime]$StartAt, [int]$Target)

    $times = @(Get-RequestTimes | Where-Object { $_ -ge $StartAt })
    $done = $times.Count
    if ($done -eq 0) {
        Write-Host "이 시각 이후 Ollama 요청이 없습니다. (워크플로라면 -Workflow 를 쓰세요)" -ForegroundColor Yellow
        return $true
    }

    $idle = (New-TimeSpan -Start $times[-1] -End (Get-Date)).TotalSeconds
    # 최근 10건으로 속도를 잰다 — 유휴 시간이 섞이지 않게.
    $window = $times | Select-Object -Last 10
    $per = if ($window.Count -ge 2) {
        (New-TimeSpan -Start $window[0] -End $window[-1]).TotalSeconds / ($window.Count - 1)
    } else { 0 }

    $left = [Math]::Max(0, $Target - $done)
    $pct = if ($Target -gt 0) { [Math]::Min(100, $done * 100 / $Target) } else { 0 }
    $bar = "#" * [int]($pct / 4) + "." * (25 - [int]($pct / 4))
    $eta = if ($per -gt 0) { "{0:N1}분" -f ($left * $per / 60) } else { "?" }

    $line = "[{0}] {1,3:N0}%  {2}/{3}  건당 {4:N2}s  남은 {5}" -f $bar, $pct, $done, $Target, $per, $eta
    if ($idle -gt 60) {
        Write-Host "$line   << $([int]$idle)초째 요청 없음 — 작업이 끝났거나 Ollama를 안 쓰는 작업입니다" -ForegroundColor Yellow
        return $true
    }
    Write-Host $line
    return ($done -ge $Target)
}

if (-not $Workflow -and $Total -le 0) {
    Write-Host "Ollama 작업을 보려면 -Total <건수>, 워크플로를 보려면 -Workflow 를 지정하세요." -ForegroundColor Yellow
    Write-Host "  예: .\scripts\watch-eval.ps1 -Total 308      (플래너 평가 154건 x 2모델)"
    Write-Host "      .\scripts\watch-eval.ps1 -Workflow"
    exit 1
}

# Ollama 모드의 시작 시각: 지정이 없으면 "60초 이상 조용하다 재개된 지점"을 시작으로 본다.
$startAt = $null
if (-not $Workflow) {
    if ($Since) {
        $startAt = [datetime]::Parse("$((Get-Date).ToString('yyyy-MM-dd')) $Since")
    }
    else {
        $times = @(Get-RequestTimes)
        if ($times.Count -eq 0) { Write-Host "Ollama 요청 기록이 없습니다." -ForegroundColor Yellow; exit 0 }
        $startAt = $times[0]
        for ($i = 1; $i -lt $times.Count; $i++) {
            if (($times[$i] - $times[$i - 1]).TotalSeconds -gt 60) { $startAt = $times[$i] }
        }
    }
    Write-Host "작업 시작 추정: $($startAt.ToString('HH:mm:ss'))  대상 건수: $Total" -ForegroundColor Cyan
}

while ($true) {
    $finished = if ($Workflow) { Show-WorkflowProgress } else { Show-OllamaProgress -StartAt $startAt -Target $Total }
    if ($Once -or $finished) { break }
    Start-Sleep -Seconds $IntervalSeconds
}
