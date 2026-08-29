use std::env;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
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

        // 이미 떠 있는지는 한 번만 짧게 찔러본다. 여기서 전체 폴링을 돌리면
        // 사이드카가 없을 때 자동 기동 시작까지 45초를 그냥 흘려보낸다.
        if !probe_health(port, &auth_token, Duration::from_millis(400)).await {
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

/// 헬스 엔드포인트를 한 번만 확인한다. 이미 떠 있는 사이드카를 빠르게 감지할 때 쓴다.
async fn probe_health(port: u16, auth_token: &str, timeout: Duration) -> bool {
    let client = reqwest::Client::new();
    let url = format!("http://127.0.0.1:{}/health", port);
    matches!(
        client.get(&url).bearer_auth(auth_token).timeout(timeout).send().await,
        Ok(resp) if resp.status().is_success()
    )
}

/// Poll the sidecar health endpoint until it responds.
async fn wait_for_ready(port: u16, auth_token: &str) -> Result<(), String> {
    const MAX_ATTEMPTS: u32 = 30;
    let client = reqwest::Client::new();
    let url = format!("http://127.0.0.1:{}/health", port);

    for attempt in 1..=MAX_ATTEMPTS {
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

    Err(format!(
        "사이드카가 {}초 안에 기동하지 못했습니다",
        MAX_ATTEMPTS * 3 / 2
    ))
}

fn resolve_uv_project_environment_dir(python_sidecar_dir: &Path) -> PathBuf {
    let from_env = env::var("UV_PROJECT_ENVIRONMENT")
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty());

    let project_root = python_sidecar_dir.parent().unwrap_or(python_sidecar_dir);
    if let Some(raw) = from_env {
        let configured = PathBuf::from(raw);
        if configured.is_absolute() {
            return configured;
        }
        return project_root.join(configured);
    }

    #[cfg(target_os = "windows")]
    {
        let local_app_data = env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                env::var_os("USERPROFILE")
                    .map(PathBuf::from)
                    .unwrap_or_else(|| PathBuf::from("."))
                    .join("AppData")
                    .join("Local")
            });
        local_app_data
            .join("officeclaw")
            .join("venvs")
            .join("python-sidecar")
    }

    #[cfg(not(target_os = "windows"))]
    {
        let home = env::var_os("HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."));
        home.join(".cache")
            .join("officeclaw")
            .join("venvs")
            .join("python-sidecar")
    }
}

fn python_executable_from_venv(venv_dir: &Path) -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    let candidate = venv_dir.join("Scripts").join("python.exe");

    #[cfg(not(target_os = "windows"))]
    let candidate = venv_dir.join("bin").join("python");

    if candidate.exists() {
        Some(candidate)
    } else {
        None
    }
}

fn spawn_dev_sidecar_process(port: u16, auth_token: &str) -> Result<(), String> {
    // tauri dev는 cargo run을 apps/desktop/src-tauri/에서 실행한다.
    // 사이드카는 모노레포에서 services/sidecar/로 이동했으므로 레포 루트 기준·크레이트 기준 후보를 모두 탐색한다.
    let python_sidecar_dir_candidates = [
        PathBuf::from("services/sidecar"), // 레포 루트에서 실행되는 경우
        PathBuf::from("../../../services/sidecar"), // apps/desktop/src-tauri에서 cargo run
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|p| p.parent())
            .and_then(|p| p.parent())
            .unwrap_or(Path::new(env!("CARGO_MANIFEST_DIR")))
            .join("services/sidecar"),
    ];
    let python_sidecar_dir = python_sidecar_dir_candidates
        .iter()
        .find(|p| p.exists())
        .cloned()
        .ok_or_else(|| {
            "Dev sidecar 자동 기동 실패: services/sidecar 디렉토리를 찾을 수 없습니다.".to_string()
        })?;

    let uv_project_environment = resolve_uv_project_environment_dir(&python_sidecar_dir);
    let local_project_venv = python_sidecar_dir.join(".venv");
    let python_cmd = [
        uv_project_environment.as_path(),
        local_project_venv.as_path(),
    ]
    .iter()
    .find_map(|venv_dir| python_executable_from_venv(venv_dir))
    .unwrap_or_else(|| PathBuf::from("python"));

    let mut cmd = ProcessCommand::new(python_cmd);
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
        .env("PYTHONIOENCODING", "utf-8")
        .env(
            "UV_PROJECT_ENVIRONMENT",
            uv_project_environment.to_string_lossy().to_string(),
        );
    let excel_engine = env::var("EXCEL_LIVE_ENGINE")
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| "auto".to_string());
    cmd.env("EXCEL_LIVE_ENGINE", excel_engine);

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
