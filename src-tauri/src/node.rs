//! Node.js 탐지 — OpenClaw(npm 글로벌 패키지)의 선행 조건.
//!
//! `ollama::is_ollama_installed` / `openclaw::is_openclaw_installed`와 동일한 패턴:
//! "바이너리가 시스템에 있는가"만 판정한다. 설치 자체는 `installer::install_node`가 담당.
//!
//! 검사 순서:
//!   1. 현재 PATH에서 `node --version` 직접 실행
//!   2. (Windows) 공식/winget 설치 위치 `%ProgramFiles%\nodejs\node.exe` 폴백
//!   3. (Unix) 로그인 셸 경유 — nvm/asdf가 PATH에 주입하는 환경 반영

use std::process::Command;

/// `node` 바이너리가 설치되어 있는지 확인.
pub async fn is_node_installed() -> serde_json::Value {
    if let Ok(output) = Command::new("node").arg("--version").output() {
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
        // GUI 앱 PATH에 nodejs 디렉터리가 없어도 설치돼 있으면 잡아낸다.
        if let Some(exe) = windows_node_exe() {
            if let Ok(output) = Command::new(&exe).arg("--version").output() {
                if output.status.success() {
                    let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
                    return serde_json::json!({
                        "installed": true,
                        "version": version,
                        "source": "program-files"
                    });
                }
            }
            // 실행은 실패해도 파일이 존재하면 설치된 것으로 간주.
            return serde_json::json!({
                "installed": true,
                "version": null,
                "source": "program-files"
            });
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
        if let Ok(output) = crate::shell::run_login_shell("node --version") {
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

/// Windows에서 Node.js 실행 파일의 표준 설치 경로를 찾는다.
/// winget(OpenJS.NodeJS.LTS)/공식 설치 모두 `%ProgramFiles%\nodejs\node.exe`에 둔다.
#[cfg(target_os = "windows")]
pub fn windows_node_exe() -> Option<std::path::PathBuf> {
    use std::path::PathBuf;
    [
        std::env::var("ProgramFiles")
            .ok()
            .map(|p| PathBuf::from(p).join("nodejs").join("node.exe")),
        std::env::var("ProgramFiles(x86)")
            .ok()
            .map(|p| PathBuf::from(p).join("nodejs").join("node.exe")),
    ]
    .into_iter()
    .flatten()
    .find(|p| p.exists())
}
