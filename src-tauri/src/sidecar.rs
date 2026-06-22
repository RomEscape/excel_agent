use std::net::TcpListener;
use std::path::PathBuf;
use std::process::Command as ProcessCommand;
use std::sync::Mutex;
use std::time::Duration;
use tauri::AppHandle;
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

/// Shared state for the sidecar connection info.
pub struct SidecarState {
    pub port: u16,
    pub auth_token: String,
}

/// Find a random available port on localhost.
fn find_available_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|e| format!("Failed to bind to random port: {}", e))?;
    let port = listener
        .local_addr()
        .map_err(|e| format!("Failed to get local addr: {}", e))?
        .port();
    Ok(port)
}

/// Generate a random auth token for sidecar communication.
fn generate_auth_token() -> String {
    use rand::Rng;
    let mut rng = rand::thread_rng();
    let bytes: Vec<u8> = (0..32).map(|_| rng.gen()).collect();
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

/// Spawn the Python sidecar and wait for it to become ready.
pub async fn spawn_sidecar(app: &AppHandle) -> Result<(), String> {
    // In dev mode, dev.sh already runs the sidecar on a fixed port.
    // 기존 프로세스가 없으면 로컬 venv로 1회 자동 기동을 시도한다.
    if cfg!(debug_assertions) {
        let port: u16 = 19532;
        let auth_token = "dev-token".to_string();
        println!(
            "[office-claw] Dev mode: connecting to sidecar on port {}",
            port
        );
        app.manage(Mutex::new(SidecarState {
            port,
            auth_token: auth_token.clone(),
        }));

        if wait_for_ready(port, &auth_token).await.is_err() {
            println!("[office-claw] Dev mode: sidecar not ready. Attempting auto-launch...");
            spawn_dev_sidecar_process(port, &auth_token)?;
            wait_for_ready(port, &auth_token).await?;
        }

        println!("[office-claw] Sidecar ready on port {}", port);
        return Ok(());
    }

    let port = find_available_port()?;
    let auth_token = generate_auth_token();

    println!("[office-claw] Starting sidecar on port {}", port);

    // Store state for IPC commands to use
    app.manage(Mutex::new(SidecarState {
        port,
        auth_token: auth_token.clone(),
    }));

    // Spawn sidecar via Tauri shell plugin
    let shell = app.shell();
    let command = shell
        .sidecar("office-claw-sidecar")
        .map_err(|e| format!("Failed to create sidecar command: {}", e))?
        .args(["--port", &port.to_string(), "--auth-token", &auth_token]);

    let (mut rx, _child) = command
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar: {}", e))?;

    // Log sidecar stdout/stderr in background
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    println!("[sidecar:out] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Stderr(line) => {
                    eprintln!("[sidecar:err] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(status) => {
                    eprintln!("[sidecar] Process terminated: {:?}", status);
                    break;
                }
                CommandEvent::Error(err) => {
                    eprintln!("[sidecar] Error: {}", err);
                }
                _ => {}
            }
        }
    });

    // Wait for sidecar to become ready
    wait_for_ready(port, &auth_token).await?;

    println!("[office-claw] Sidecar running on port {}", port);
    Ok(())
}

/// Poll the sidecar health endpoint until it responds.
async fn wait_for_ready(port: u16, auth_token: &str) -> Result<(), String> {
    let client = reqwest::Client::new();
    let url = format!("http://127.0.0.1:{}/health", port);

    for attempt in 1..=30 {
        match client
            .get(&url)
            .bearer_auth(auth_token)
            .timeout(Duration::from_secs(1))
            .send()
            .await
        {
            Ok(resp) if resp.status().is_success() => {
                println!("[office-claw] Sidecar ready after {} attempts", attempt);
                return Ok(());
            }
            Ok(resp) => {
                println!(
                    "[office-claw] Health check attempt {}: status {}",
                    attempt,
                    resp.status()
                );
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
            Err(e) => {
                println!("[office-claw] Health check attempt {}: {}", attempt, e);
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        }
    }

    Err("Sidecar failed to start within 15 seconds".to_string())
}

fn spawn_dev_sidecar_process(port: u16, auth_token: &str) -> Result<(), String> {
    // tauri dev는 cargo run을 src-tauri/에서 실행하므로 상위 경로(../python-sidecar)도 함께 탐색한다.
    let dir_candidates = [
        PathBuf::from("python-sidecar"),
        PathBuf::from("../python-sidecar"),
    ];
    let python_sidecar_dir = dir_candidates
        .into_iter()
        .find(|p| p.exists())
        .ok_or_else(|| {
            "Dev sidecar 자동 기동 실패: python-sidecar 디렉토리를 찾을 수 없습니다.".to_string()
        })?;

    // venv 실행 파일도 위에서 찾은 디렉토리 기준으로 해석한다(cwd 의존 상대경로 제거).
    let venv_candidates = [
        python_sidecar_dir.join(".venv/Scripts/python.exe"), // Windows
        python_sidecar_dir.join(".venv/bin/python"),         // macOS/Linux
    ];
    let python_cmd = venv_candidates
        .into_iter()
        .find(|p| p.exists())
        .unwrap_or_else(|| PathBuf::from("python"));

    let mut cmd = ProcessCommand::new(&python_cmd);
    cmd.current_dir(&python_sidecar_dir)
        .args([
            "-m",
            "office_claw_sidecar",
            "--port",
            &port.to_string(),
            "--auth-token",
            auth_token,
        ])
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8");

    if let Some(token) = load_gateway_token_from_openclaw_config() {
        if !token.is_empty() {
            cmd.env("OPENCLAW_GATEWAY_TOKEN", token);
        }
    }

    cmd.spawn()
        .map_err(|e| format!("Dev sidecar 자동 기동 실패: {}", e))?;
    Ok(())
}

fn load_gateway_token_from_openclaw_config() -> Option<String> {
    if let Ok(token) = std::env::var("OPENCLAW_GATEWAY_TOKEN") {
        let t = token.trim().to_string();
        if !t.is_empty() {
            return Some(t);
        }
    }

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
