//! OpenClaw 게이트웨이 프로세스 라이프사이클 관리.
//!
//! Tauri 앱 시작 시 OpenClaw 게이트웨이(Node.js)를 자식 프로세스로 시작하고,
//! 앱 종료 시 정리한다.
//!
//! 통신 방식:
//!   Python sidecar ←WebSocket→ OpenClaw gateway (ws://127.0.0.1:18789)
//!
//! dev 모드에서는 이미 외부에서 실행 중인 게이트웨이를 그대로 사용한다.

use std::path::PathBuf;
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
pub async fn spawn_openclaw(state: &tauri::State<'_, Mutex<OpenClawState>>) -> Result<(), String> {
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
    // sidecar(OpenClaw client)와 gateway 인증 토큰을 맞추기 위해
    // OPENCLAW_GATEWAY_TOKEN을 명시적으로 주입한다.
    // - 환경변수에 값이 있으면 그 값을 사용
    // - 없으면 ~/.openclaw/openclaw.json의 gateway.auth.token을 사용
    // - dev 환경에서는 기본값 dev-token 사용
    let gateway_token = std::env::var("OPENCLAW_GATEWAY_TOKEN").unwrap_or_else(|_| {
        if let Some(token) = load_gateway_token_from_openclaw_config() {
            return token;
        }
        if cfg!(debug_assertions) {
            "dev-token".to_string()
        } else {
            String::new()
        }
    });

    // 1차: 직접 실행
    let mut direct_cmd = Command::new("openclaw");
    if !gateway_token.is_empty() {
        direct_cmd.env("OPENCLAW_GATEWAY_TOKEN", &gateway_token);
    }
    let direct = direct_cmd
        .args(["gateway", "--port", &port_str])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
    if let Ok(child) = direct {
        return Ok(child);
    }

    #[cfg(target_os = "windows")]
    {
        // 2차(Windows): npm global/openclaw 커스텀 prefix 경로 직접 실행
        for openclaw_cmd in windows_openclaw_cmd_candidates() {
            if openclaw_cmd.exists() {
                let mut cmd = Command::new(&openclaw_cmd);
                if !gateway_token.is_empty() {
                    cmd.env("OPENCLAW_GATEWAY_TOKEN", &gateway_token);
                }
                if let Ok(child) = cmd
                    .args(["gateway", "--port", &port_str])
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .spawn()
                {
                    return Ok(child);
                }
            }
        }

        // 3차(Windows): PowerShell에서 실행 (GUI PATH 불일치 우회)
        let ps_cmd = format!("openclaw gateway --port {}", port_str);
        let mut ps = Command::new("powershell");
        if !gateway_token.is_empty() {
            ps.env("OPENCLAW_GATEWAY_TOKEN", &gateway_token);
        }
        if let Ok(child) = ps
            .args(["-NoProfile", "-Command", &ps_cmd])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
        {
            return Ok(child);
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
        // 2차(Unix): 사용자 로그인 셸 — Tauri GUI는 nvm PATH가 안 잡히는 경우가 많음
        let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
        let shell_cmd = format!("openclaw gateway --port {}", port_str);
        let mut via_shell_cmd = Command::new(&shell);
        if !gateway_token.is_empty() {
            via_shell_cmd.env("OPENCLAW_GATEWAY_TOKEN", &gateway_token);
        }
        let via_shell = via_shell_cmd
            .args(["-lc", &shell_cmd])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();
        if let Ok(child) = via_shell {
            return Ok(child);
        }
    }

    // 마지막 폴백: npx
    let mut npx_cmd = Command::new("npx");
    if !gateway_token.is_empty() {
        npx_cmd.env("OPENCLAW_GATEWAY_TOKEN", &gateway_token);
    }
    let npx = npx_cmd
        .args(["--yes", "openclaw", "gateway", "--port", &port_str])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("openclaw/npx 실행 실패 (Node.js가 설치되어 있나요?): {}", e))?;
    Ok(npx)
}

fn load_gateway_token_from_openclaw_config() -> Option<String> {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)?;
    let cfg_path = home.join(".openclaw").join("openclaw.json");
    if !cfg_path.exists() {
        return None;
    }

    let raw = std::fs::read_to_string(cfg_path).ok()?;
    let json: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let token = json
        .get("gateway")
        .and_then(|v| v.get("auth"))
        .and_then(|v| v.get("token"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if token.is_empty() {
        None
    } else {
        Some(token)
    }
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

    #[cfg(target_os = "windows")]
    {
        // 2차(Windows): npm global/openclaw 커스텀 prefix 경로 직접 확인
        for openclaw_cmd in windows_openclaw_cmd_candidates() {
            if openclaw_cmd.exists() {
                if let Ok(output) = Command::new(&openclaw_cmd).arg("--version").output() {
                    if output.status.success() {
                        let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
                        return serde_json::json!({
                            "installed": true,
                            "version": version,
                            "source": format!("cmd-path:{}", openclaw_cmd.to_string_lossy())
                        });
                    }
                }
            }
        }

        // 3차(Windows): PowerShell 경유 확인
        if let Ok(output) = Command::new("powershell")
            .args(["-NoProfile", "-Command", "openclaw --version"])
            .output()
        {
            if output.status.success() {
                let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
                return serde_json::json!({
                    "installed": true,
                    "version": version,
                    "source": "powershell"
                });
            }
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
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
    }

    serde_json::json!({
        "installed": false,
        "version": null,
        "source": null
    })
}

#[cfg(target_os = "windows")]
fn windows_openclaw_cmd_candidates() -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();

    // 0) npm runtime prefix 조회 (사용자별 커스텀 prefix 대응)
    for prefix in windows_npm_prefix_candidates() {
        if !prefix.trim().is_empty() {
            out.push(PathBuf::from(&prefix).join("openclaw.cmd"));
            out.push(PathBuf::from(&prefix).join("bin").join("openclaw.cmd"));
        }
    }

    if let Ok(prefix) = std::env::var("NPM_CONFIG_PREFIX") {
        if !prefix.trim().is_empty() {
            out.push(PathBuf::from(&prefix).join("openclaw.cmd"));
            out.push(PathBuf::from(&prefix).join("bin").join("openclaw.cmd"));
        }
    }

    if let Ok(appdata) = std::env::var("APPDATA") {
        out.push(PathBuf::from(appdata).join("npm").join("openclaw.cmd"));
    }

    if let Ok(userprofile) = std::env::var("USERPROFILE") {
        let user_home = PathBuf::from(userprofile);
        out.push(user_home.join(".npm-global").join("openclaw.cmd"));
        out.push(
            user_home
                .join(".npm-global")
                .join("bin")
                .join("openclaw.cmd"),
        );
    }

    // 순서 유지 + 중복 제거
    let mut uniq: Vec<PathBuf> = Vec::new();
    for p in out {
        if !uniq.iter().any(|u| u == &p) {
            uniq.push(p);
        }
    }
    uniq
}

#[cfg(target_os = "windows")]
fn windows_npm_prefix_candidates() -> Vec<String> {
    let mut out: Vec<String> = Vec::new();

    // npm이 PATH에 있으면 가장 신뢰도 높은 실제 prefix를 얻는다.
    if let Ok(output) = Command::new("npm")
        .args(["config", "get", "prefix"])
        .output()
    {
        if output.status.success() {
            let prefix = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !prefix.is_empty() && prefix != "undefined" && prefix != "null" {
                out.push(prefix);
            }
        }
    }

    // GUI PATH에서 npm 탐색이 실패하는 경우를 위해 PowerShell 경유도 한 번 더 시도.
    if let Ok(output) = Command::new("powershell")
        .args(["-NoProfile", "-Command", "npm config get prefix"])
        .output()
    {
        if output.status.success() {
            let prefix = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !prefix.is_empty() && prefix != "undefined" && prefix != "null" {
                out.push(prefix);
            }
        }
    }

    // 중복 제거
    let mut uniq: Vec<String> = Vec::new();
    for p in out {
        if !uniq.iter().any(|u| u == &p) {
            uniq.push(p);
        }
    }
    uniq
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
            "reason_code": "RUNNING_OK",
            "port": port,
            "message": format!("OpenClaw 게이트웨이 실행 중 (포트 {})", port)
        })
    } else if running && !reachable {
        serde_json::json!({
            "state": "error",
            "reason_code": "PROCESS_RUNNING_PORT_UNREACHABLE",
            "port": port,
            "message": "OpenClaw 게이트웨이가 응답하지 않습니다"
        })
    } else {
        let install_info = is_openclaw_installed().await;
        let installed = install_info
            .get("installed")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let reason_code = if installed {
            "INSTALLED_BUT_STOPPED"
        } else {
            "OPENCLAW_NOT_INSTALLED_OR_PATH_MISMATCH"
        };
        serde_json::json!({
            "state": "stopped",
            "reason_code": reason_code,
            "port": port,
            "installed_hint": installed,
            "message": "OpenClaw 게이트웨이가 실행되지 않았습니다. npm install -g openclaw@latest 를 실행해 주세요."
        })
    }
}
