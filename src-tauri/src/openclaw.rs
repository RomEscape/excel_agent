//! OpenClaw 게이트웨이 프로세스 라이프사이클 관리.
//!
//! Tauri 앱 시작 시 OpenClaw 게이트웨이(Node.js)를 자식 프로세스로 시작하고,
//! 앱 종료 시 정리한다.
//!
//! 통신 방식:
//!   Python sidecar ←WebSocket→ OpenClaw gateway (ws://127.0.0.1:18789)
//!
//! dev 모드에서는 이미 외부에서 실행 중인 게이트웨이를 그대로 사용한다.

use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

/// OpenClaw 게이트웨이 상태.
pub struct OpenClawState {
    /// 게이트웨이가 리스닝 중인 포트 (기본 18789)
    pub port: u16,
    /// 현재 게이트웨이가 실행 중인지 여부
    pub running: bool,
    /// 자식 프로세스 핸들 (프로덕션 모드에서만 Some)
    pub child: Option<Child>,
}

impl Default for OpenClawState {
    fn default() -> Self {
        OpenClawState {
            port: 18789,
            running: false,
            child: None,
        }
    }
}

impl Drop for OpenClawState {
    fn drop(&mut self) {
        if let Some(ref mut child) = self.child {
            let _ = child.kill();
        }
    }
}

/// OpenClaw 게이트웨이가 18789에서 응답할 때까지 보장한다.
///
/// 동작 (idempotent):
///   1. 이미 게이트웨이가 살아있으면 state.running=true로 sync 후 즉시 반환
///   2. 아니면 `openclaw gateway --port PORT` 자식 프로세스를 spawn
///   3. 최대 30초 health-poll 후 결과 반환
///
/// 사용자 요청에 의해 dev/prod 동일하게 spawn-and-go 방식으로 통일.
pub async fn spawn_openclaw(
    state: &tauri::State<'_, Mutex<OpenClawState>>,
) -> Result<(), String> {
    let port = {
        let s = state.lock().map_err(|e| e.to_string())?;
        s.port
    };

    // 이미 떠 있으면 즉시 OK — 자식 프로세스 중복 spawn 방지 (idempotent)
    if is_gateway_ready(port).await {
        println!("[openclaw] Gateway already running on port {}", port);
        let mut s = state.lock().map_err(|e| e.to_string())?;
        s.running = true;
        return Ok(());
    }

    println!("[openclaw] Spawning gateway on port {}", port);
    let child_result = spawn_gateway_process(port);
    match child_result {
        Ok(child) => {
            {
                let mut s = state.lock().map_err(|e| e.to_string())?;
                // 이전 child가 좀비로 남아있으면 정리
                if let Some(ref mut old) = s.child {
                    let _ = old.kill();
                }
                s.child = Some(child);
            }
            // 게이트웨이가 준비될 때까지 폴링 (최대 30초)
            wait_for_gateway(port).await?;
            let mut s = state.lock().map_err(|e| e.to_string())?;
            s.running = true;
            println!("[openclaw] Gateway started on port {}", port);
            Ok(())
        }
        Err(e) => {
            // OpenClaw 미설치 또는 Node.js 없음 → 사용자에게 안내 필요
            eprintln!("[openclaw] Failed to start gateway: {}", e);
            Err(format!(
                "OpenClaw 게이트웨이 시작 실패: {}\n\n'npm install -g openclaw@latest' 를 실행하거나 온보딩에서 설치 안내를 따라주세요.",
                e
            ))
        }
    }
}

/// openclaw 게이트웨이 프로세스를 실행한다.
///
/// 설치 방식에 따라 다음 순서로 시도:
///   1. `openclaw gateway` (글로벌 설치, 시스템 PATH)
///   2. 로그인 셸을 통해 `openclaw gateway` (nvm/asdf 등 셸 init이 PATH 주입하는 환경)
///   3. `npx --yes openclaw gateway` (마지막 폴백)
fn spawn_gateway_process(port: u16) -> Result<Child, String> {
    let port_str = port.to_string();

    // 1차: 직접 실행
    let direct = Command::new("openclaw")
        .args(["gateway", "--port", &port_str])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
    if let Ok(child) = direct {
        return Ok(child);
    }

    // 2차: 사용자 로그인 셸 — Tauri GUI는 nvm PATH가 안 잡히는 경우가 많음
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
    let shell_cmd = format!("openclaw gateway --port {}", port_str);
    let via_shell = Command::new(&shell)
        .args(["-lc", &shell_cmd])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
    if let Ok(child) = via_shell {
        return Ok(child);
    }

    // 3차: npx
    let npx = Command::new("npx")
        .args(["--yes", "openclaw", "gateway", "--port", &port_str])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("openclaw/npx 실행 실패 (Node.js가 설치되어 있나요?): {}", e))?;
    Ok(npx)
}

/// 게이트웨이 HTTP health 엔드포인트를 최대 30초간 폴링한다.
async fn wait_for_gateway(port: u16) -> Result<(), String> {
    for attempt in 1..=60 {
        if is_gateway_ready(port).await {
            println!("[openclaw] Gateway ready after {} attempts", attempt);
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    Err(format!(
        "OpenClaw 게이트웨이가 30초 내에 응답하지 않습니다 (port {})",
        port
    ))
}

/// 게이트웨이 포트에 TCP 연결 가능 여부를 확인한다.
/// OpenClaw는 별도의 HTTP health 엔드포인트가 없으므로 TCP 연결로 확인한다.
async fn is_gateway_ready(port: u16) -> bool {
    use tokio::net::TcpStream;
    use tokio::time::timeout;

    let addr = format!("127.0.0.1:{}", port);
    matches!(
        timeout(Duration::from_millis(300), TcpStream::connect(&addr)).await,
        Ok(Ok(_))
    )
}

/// `openclaw` 바이너리가 시스템에 설치되어 있는지 확인한다.
///
/// 게이트웨이 실행 여부와 무관하게 "패키지가 설치되어 있는가"만 판정한다.
/// 자동 설치 UI를 띄울지 결정할 때 이 함수를 사용한다.
///
/// 검사 순서:
///   1. 현재 PATH에서 `openclaw --version` 직접 실행
///   2. 사용자 로그인 셸을 통해 실행 (nvm/asdf 등 셸 초기화 후의 PATH 반영)
pub async fn is_openclaw_installed() -> serde_json::Value {
    use std::process::Command;

    if let Ok(output) = Command::new("openclaw").arg("--version").output() {
        if output.status.success() {
            let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
            return serde_json::json!({
                "installed": true,
                "version": version,
                "source": "path"
            });
        }
    }

    // GUI 앱은 nvm 등이 초기화된 PATH를 못 받는 경우가 많아 로그인 셸을 한 번 더 시도한다.
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
    if let Ok(output) = Command::new(&shell)
        .args(["-lc", "openclaw --version"])
        .output()
    {
        if output.status.success() {
            let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
            return serde_json::json!({
                "installed": true,
                "version": version,
                "source": "login-shell"
            });
        }
    }

    serde_json::json!({
        "installed": false,
        "version": null,
        "source": null
    })
}

/// 현재 OpenClaw 게이트웨이 상태를 반환한다.
///
/// React UI에서 상태 표시용으로 사용.
pub async fn get_openclaw_status(
    state: &tauri::State<'_, Mutex<OpenClawState>>,
) -> serde_json::Value {
    let (port, running) = {
        match state.lock() {
            Ok(s) => (s.port, s.running),
            Err(_) => return serde_json::json!({ "state": "error", "message": "상태 읽기 실패" }),
        }
    };

    // 실제로 연결 가능한지 재확인
    let reachable = is_gateway_ready(port).await;

    if running && reachable {
        serde_json::json!({
            "state": "running",
            "port": port,
            "message": format!("OpenClaw 게이트웨이 실행 중 (포트 {})", port)
        })
    } else if running && !reachable {
        serde_json::json!({
            "state": "error",
            "port": port,
            "message": "OpenClaw 게이트웨이가 응답하지 않습니다"
        })
    } else {
        serde_json::json!({
            "state": "stopped",
            "port": port,
            "message": "OpenClaw 게이트웨이가 실행되지 않았습니다. npm install -g openclaw@latest 를 실행해 주세요."
        })
    }
}
