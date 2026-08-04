//! Ollama 로컬 LLM 라이프사이클 헬퍼.
//!
//! 우리는 Ollama 데몬 자체는 시작하지 않는다 — 사용자가 직접 켜둔 Ollama에 HTTP로 붙는다.
//! 단 *바이너리 설치 여부*와 *실행 상태(11434)*, *설치된 모델 목록*은 감지한다.
//!
//! 모델 pull과 brew/winget 설치는 frontend에서 `@tauri-apps/plugin-shell`로 직접
//! 스트리밍 처리한다 (capabilities/default.json에 명령 화이트리스트).

use std::process::Command;
use std::time::Duration;

/// Ollama 데몬 기본 포트
pub const OLLAMA_PORT: u16 = 11434;

/// `ollama` CLI가 설치되어 있는지 확인.
pub async fn is_ollama_installed() -> serde_json::Value {
    if let Ok(output) = Command::new("ollama").arg("--version").output() {
        if output.status.success() {
            let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
            return serde_json::json!({
                "installed": true,
                "version": version,
                "source": "path"
            });
        }
    }
    // GUI 앱은 nvm/asdf 등이 주입한 PATH를 못 받는 경우가 많아 로그인 셸로 한 번 더 시도.
    #[cfg(not(target_os = "windows"))]
    {
        if let Ok(output) = crate::shell::run_login_shell("ollama --version") {
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

    // Windows GUI 앱은 PATH에 Ollama 설치 폴더가 없는 경우가 많아 직접 실행이 실패한다.
    // 표준 설치 위치를 확인해 "설치돼 있는데 미설치로 보여서 다시 설치하는" 문제를 막는다.
    #[cfg(target_os = "windows")]
    {
        if let Some(exe) = windows_ollama_exe() {
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

    serde_json::json!({ "installed": false, "version": null, "source": null })
}

/// Windows에서 Ollama 실행 파일(CLI)의 표준 설치 경로를 찾는다.
/// winget(Ollama.Ollama)/공식 설치 모두 아래 위치에 `ollama.exe`를 둔다.
/// 탐지와 데몬 시작이 같은 경로를 바라보도록 단일 소스로 둔다.
#[cfg(target_os = "windows")]
pub fn windows_ollama_exe() -> Option<std::path::PathBuf> {
    use std::path::PathBuf;
    [
        std::env::var("LOCALAPPDATA").ok().map(|p| {
            PathBuf::from(p)
                .join("Programs")
                .join("Ollama")
                .join("ollama.exe")
        }),
        std::env::var("ProgramFiles")
            .ok()
            .map(|p| PathBuf::from(p).join("Ollama").join("ollama.exe")),
    ]
    .into_iter()
    .flatten()
    .find(|p| p.exists())
}

/// Ollama 데몬이 11434에서 응답하는지 확인 (HTTP /api/tags).
pub async fn is_ollama_running() -> bool {
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_millis(500))
        .build()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    let url = format!("http://127.0.0.1:{}/api/tags", OLLAMA_PORT);
    matches!(client.get(&url).send().await, Ok(r) if r.status().is_success())
}

/// 설치된 Ollama 모델 목록 — HTTP /api/tags를 그대로 반환한다.
/// 실패 시 빈 리스트.
pub async fn list_ollama_models() -> serde_json::Value {
    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()
    {
        Ok(c) => c,
        Err(_) => return serde_json::json!({ "models": [] }),
    };
    let url = format!("http://127.0.0.1:{}/api/tags", OLLAMA_PORT);
    match client.get(&url).send().await {
        Ok(resp) if resp.status().is_success() => match resp.json::<serde_json::Value>().await {
            Ok(json) => json,
            Err(_) => serde_json::json!({ "models": [] }),
        },
        _ => serde_json::json!({ "models": [] }),
    }
}

/// 종합 상태 — UI 단일 호출로 모든 정보 받기.
pub async fn get_ollama_status() -> serde_json::Value {
    let installed_info = is_ollama_installed().await;
    let running = is_ollama_running().await;
    // 데몬이 11434에서 응답하면 바이너리 탐지가 실패했더라도 설치된 것으로 간주.
    let installed = installed_info["installed"].as_bool().unwrap_or(false) || running;
    let models = if running {
        list_ollama_models().await
    } else {
        serde_json::json!({ "models": [] })
    };
    serde_json::json!({
        "installed": installed,
        "version": installed_info["version"],
        "running": running,
        "port": OLLAMA_PORT,
        "models": models["models"],
    })
}

/// OpenClaw 설정을 Ollama provider 기준으로 맞춘다.
///
/// 적용 항목:
/// - models.providers.ollama.api
/// - models.providers.ollama.baseUrl
/// - models.providers.ollama.apiKey
/// - agents.defaults.model
pub async fn configure_openclaw_ollama(model: &str) -> Result<serde_json::Value, String> {
    validate_model_name(model)?;
    let model_name = model.trim();
    let full_model = if model_name.starts_with("ollama/") {
        model_name.to_string()
    } else {
        format!("ollama/{}", model_name)
    };

    let commands = vec![
        "openclaw configure set models.providers.ollama.api ollama".to_string(),
        "openclaw configure set models.providers.ollama.baseUrl http://127.0.0.1:11434".to_string(),
        "openclaw configure set models.providers.ollama.apiKey ollama-local".to_string(),
        format!(
            "openclaw configure set agents.defaults.model {}",
            full_model
        ),
    ];

    let mut applied: Vec<String> = Vec::new();
    for cmd in commands {
        let out = crate::shell::run_login_shell(&cmd)
            .map_err(|e| format!("OpenClaw 설정 명령 실행 실패: {}", e))?;
        if !out.status.success() {
            let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
            let msg = if stderr.is_empty() {
                format!("OpenClaw 설정 명령 실패: {}", cmd)
            } else {
                format!("OpenClaw 설정 명령 실패: {} ({})", cmd, stderr)
            };
            return Err(msg);
        }
        applied.push(cmd);
    }

    Ok(serde_json::json!({
        "ok": true,
        "model": full_model,
        "applied": applied,
    }))
}

/// 모델명에 셸 메타문자가 들어있지 않은지 검증.
///
/// `phi3.5`, `llama3.2:latest`, `qwen2.5:7b-instruct` 같은 합법적인 Ollama 태그는 허용하고,
/// 공백/세미콜론/파이프/달러/백틱/백슬래시 등 명령 인젝션 가능 문자는 거부.
pub(crate) fn validate_model_name(model: &str) -> Result<(), String> {
    if model.is_empty()
        || model
            .chars()
            .any(|c| c.is_whitespace() || matches!(c, ';' | '|' | '&' | '$' | '`' | '\\'))
    {
        return Err(format!("모델 이름이 유효하지 않습니다: {}", model));
    }
    Ok(())
}

// ── 단위 테스트 ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_accepts_common_ollama_tags() {
        for ok in [
            "phi3.5",
            "phi3.5:latest",
            "llama3.2",
            "llama3.2:3b-instruct-q4_0",
            "qwen2.5:7b",
            "gemma2:2b",
            "gemma4:e4b",
        ] {
            assert!(
                validate_model_name(ok).is_ok(),
                "정상 모델명을 거부함: {}",
                ok
            );
        }
    }

    #[test]
    fn validate_rejects_empty_string() {
        assert!(validate_model_name("").is_err());
    }

    #[test]
    fn validate_rejects_shell_metachars() {
        // 명령 인젝션 가능 문자 차단 — Tauri shell scope를 거치지 않고 호출되는 경로 보호
        for bad in [
            "phi3.5; rm -rf /",
            "phi3.5 && curl evil.com",
            "phi3.5 | nc evil 1234",
            "phi3.5`whoami`",
            "phi3.5$IFS",
            "phi3.5\\nrm",
            " phi3.5",  // leading whitespace
            "phi3.5\n", // trailing newline
            "phi 3.5",  // embedded space
        ] {
            assert!(
                validate_model_name(bad).is_err(),
                "위험한 모델명을 통과시킴: {:?}",
                bad
            );
        }
    }

    #[test]
    fn ollama_port_is_default_llm_port() {
        // 11434는 Ollama 공식 기본 포트 — 변경 시 sidecar/config도 같이 손봐야 함을 알리는 가드
        assert_eq!(OLLAMA_PORT, 11434);
    }
}
