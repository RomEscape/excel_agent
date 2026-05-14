use std::net::TcpListener;
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
    // Skip launching the binary and connect to the existing process.
    if cfg!(debug_assertions) {
        let port: u16 = 19532;
        let auth_token = "dev-token".to_string();
        println!("[office-claw] Dev mode: connecting to sidecar on port {}", port);
        app.manage(Mutex::new(SidecarState {
            port,
            auth_token: auth_token.clone(),
        }));
        wait_for_ready(port, &auth_token).await?;
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
