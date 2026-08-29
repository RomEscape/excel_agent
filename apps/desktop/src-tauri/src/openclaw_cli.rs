//! OpenClaw 게이트웨이 CLI 서브프로세스 wrapper (2026-05-20).
//!
//! 옵션 C: `openclaw gateway call <method> --json --params <json>`을 spawn해
//! stdout JSON을 파싱한다. WebSocket을 우리가 직접 다루는 대신 OpenClaw 자체
//! CLI가 핸드셰이크/auth/세션을 알아서 처리하도록 위임 — 게이트웨이 마이너
//! 버전이 바뀌어도 우리 wrapper 코드는 영향받지 않는다.
//!
//! ## 메서드 카테고리
//!   - `gateway call <method>` — health, system-presence, status, cron.* 등
//!   - `agent` (top-level) — 에이전트 한 턴 실행 (메신저 봇 메시지 처리에 사용)
//!
//! ## 인증
//!   - 게이트웨이가 `--token` 모드면 `OPENCLAW_GATEWAY_TOKEN` 환경변수를 전달
//!   - device pairing이나 Ollama 프로바이더 등록은 사용자가 `openclaw configure`
//!     로 사전에 처리. 이 wrapper의 책임 외.
//!
//! ## 메신저 봇 시나리오 적합성
//!   호출당 서브프로세스 spawn 오버헤드 ~500ms-1s. LLM 생성 시간(수 초 단위)에
//!   묻혀서 사용자 체감 영향 없음. WebSocket 직결은 과스펙이라 채택 안 함.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::time::Duration;
use tokio::process::Command;

/// `openclaw` 실행 파일 경로 해석.
///
/// macOS GUI(Finder/Dock) 실행 앱은 launchd의 최소 PATH만 상속받아 npm-global·
/// homebrew의 openclaw를 못 찾는다(2026-08-30 macOS 감사 — openclaw.rs는 로그인 셸
/// 폴백이 있는데 이 모듈만 없어 게이트웨이 호출이 GUI에서 SpawnFailed였다).
/// 셸 문자열 재조립은 인자 검증을 무의미하게 하므로, 로그인 셸에는 **경로만** 묻고
/// 구조화 인자는 그대로 둔다. 결과는 프로세스 수명 동안 캐시한다.
#[cfg(not(target_os = "windows"))]
fn resolve_openclaw_bin() -> String {
    use std::sync::OnceLock;
    static BIN: OnceLock<String> = OnceLock::new();
    BIN.get_or_init(|| {
        let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
        if let Ok(out) = std::process::Command::new(&shell)
            .args(["-lc", "command -v openclaw"])
            .output()
        {
            if out.status.success() {
                let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
                if !path.is_empty() {
                    return path;
                }
            }
        }
        "openclaw".to_string()
    })
    .clone()
}

#[cfg(target_os = "windows")]
fn resolve_openclaw_bin() -> String {
    "openclaw".to_string()
}

const DEFAULT_TIMEOUT_MS: u64 = 30_000;
const AGENT_TIMEOUT_MS: u64 = 120_000;

#[derive(Debug)]
pub enum OpenClawCliError {
    /// CLI 자체가 실행 안 됨 (PATH에 없음 등)
    SpawnFailed(String),
    /// CLI는 실행됐는데 non-zero exit
    NonZeroExit { code: Option<i32>, stderr: String },
    /// stdout이 JSON으로 파싱 안 됨
    InvalidJson { stdout: String, error: String },
    /// timeout
    Timeout(u64),
}

impl std::fmt::Display for OpenClawCliError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OpenClawCliError::SpawnFailed(m) => {
                write!(f, "openclaw CLI 실행 실패: {}", m)
            }
            OpenClawCliError::NonZeroExit { code, stderr } => {
                write!(f, "openclaw CLI 오류 (exit {:?}): {}", code, stderr.trim())
            }
            OpenClawCliError::InvalidJson { stdout, error } => write!(
                f,
                "openclaw stdout JSON 파싱 실패: {} (raw: {:.300})",
                error, stdout
            ),
            OpenClawCliError::Timeout(ms) => {
                write!(f, "openclaw CLI 타임아웃 ({}ms)", ms)
            }
        }
    }
}

impl std::error::Error for OpenClawCliError {}

impl OpenClawCliError {
    pub fn into_string(self) -> String {
        self.to_string()
    }
}

/// `openclaw gateway call <method>` 호출 옵션.
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct CallOpts {
    /// 게이트웨이 토큰 — 게이트웨이가 `--auth token`이면 필수. `OPENCLAW_GATEWAY_TOKEN`이 더 우선.
    pub token: Option<String>,
    /// 게이트웨이 password (드물게 사용).
    pub password: Option<String>,
    /// WebSocket URL 오버라이드.
    pub url: Option<String>,
    /// 호출당 타임아웃(ms).
    pub timeout_ms: Option<u64>,
    /// agent run 시 "final response 대기" 플래그.
    pub expect_final: bool,
}

/// `openclaw gateway call <method> --json [--params JSON]` 실행.
///
/// 성공 시 stdout을 JSON으로 파싱해 반환.
pub async fn gateway_call(
    method: &str,
    params: Option<&Value>,
    opts: &CallOpts,
) -> Result<Value, OpenClawCliError> {
    validate_method(method)?;
    let mut cmd = Command::new(resolve_openclaw_bin());
    cmd.args(["gateway", "call", method, "--json"]);

    if let Some(p) = params {
        cmd.args(["--params", &p.to_string()]);
    }
    apply_common_opts(&mut cmd, opts);

    let timeout_ms = opts.timeout_ms.unwrap_or(DEFAULT_TIMEOUT_MS);
    run_and_parse(cmd, timeout_ms).await
}

/// `openclaw agent ...` — 에이전트 한 턴 실행 (메신저 봇 메시지 처리에 사용).
///
/// `to`/`session_id`/`agent` 중 적어도 하나는 채워야 게이트웨이가 세션을 식별할 수 있다.
/// `channel`이 비어 있으면 main 세션 채널 사용.
pub async fn agent_turn(req: &AgentTurnRequest) -> Result<Value, OpenClawCliError> {
    if req.to.is_none() && req.session_id.is_none() && req.agent.is_none() {
        return Err(OpenClawCliError::SpawnFailed(
            "to/session_id/agent 중 하나는 필수".into(),
        ));
    }

    let mut cmd = Command::new(resolve_openclaw_bin());
    cmd.args(["agent", "--json", "-m", &req.message]);

    if let Some(a) = &req.agent {
        validate_simple_arg(a, "agent")?;
        cmd.args(["--agent", a]);
    }
    if let Some(s) = &req.session_id {
        validate_simple_arg(s, "session-id")?;
        cmd.args(["--session-id", s]);
    }
    if let Some(t) = &req.to {
        validate_simple_arg(t, "to")?;
        cmd.args(["-t", t]);
    }
    if let Some(c) = &req.channel {
        validate_simple_arg(c, "channel")?;
        cmd.args(["--channel", c]);
    }
    if let Some(m) = &req.model {
        validate_simple_arg(m, "model")?;
        cmd.args(["--model", m]);
    }
    if req.deliver {
        cmd.arg("--deliver");
    }
    apply_common_opts(&mut cmd, &req.opts);

    let timeout_ms = req.opts.timeout_ms.unwrap_or(AGENT_TIMEOUT_MS);
    run_and_parse(cmd, timeout_ms).await
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AgentTurnRequest {
    pub message: String,
    pub agent: Option<String>,
    pub session_id: Option<String>,
    pub to: Option<String>,
    pub channel: Option<String>,
    pub model: Option<String>,
    pub deliver: bool,
    #[serde(default)]
    pub opts: CallOpts,
}

// ── 내부 ─────────────────────────────────────────────────────────────────────

fn apply_common_opts(cmd: &mut Command, opts: &CallOpts) {
    if let Some(t) = &opts.token {
        if validate_simple_arg(t, "token").is_ok() {
            cmd.args(["--token", t]);
        }
    }
    if let Some(p) = &opts.password {
        cmd.args(["--password", p]);
    }
    if let Some(u) = &opts.url {
        if validate_simple_arg(u, "url").is_ok() {
            cmd.args(["--url", u]);
        }
    }
    if opts.expect_final {
        cmd.arg("--expect-final");
    }
    // 환경변수는 그대로 상속됨 — OPENCLAW_GATEWAY_TOKEN 등을 셸이 들고 있다면 자동 사용
}

async fn run_and_parse(mut cmd: Command, timeout_ms: u64) -> Result<Value, OpenClawCliError> {
    cmd.kill_on_drop(true);
    let child = cmd
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| OpenClawCliError::SpawnFailed(e.to_string()))?;

    let output = tokio::time::timeout(Duration::from_millis(timeout_ms), child.wait_with_output())
        .await
        .map_err(|_| OpenClawCliError::Timeout(timeout_ms))?
        .map_err(|e| OpenClawCliError::SpawnFailed(format!("wait_with_output: {}", e)))?;

    if !output.status.success() {
        return Err(OpenClawCliError::NonZeroExit {
            code: output.status.code(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        });
    }

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    // OpenClaw가 stdout에 진단 메시지를 같이 흘리는 경우가 있으므로 마지막 JSON 객체만 추출.
    let json_str = extract_last_json_blob(&stdout).unwrap_or_else(|| stdout.clone());
    serde_json::from_str::<Value>(&json_str).map_err(|e| OpenClawCliError::InvalidJson {
        stdout,
        error: e.to_string(),
    })
}

/// stdout에서 마지막 균형잡힌 `{...}` 또는 `[...]` 블록을 찾아 반환.
/// OpenClaw가 진단 로그를 stdout에 섞어 흘리는 경우에 대비.
fn extract_last_json_blob(s: &str) -> Option<String> {
    let bytes = s.as_bytes();
    let mut end: Option<usize> = None;
    for i in (0..bytes.len()).rev() {
        let b = bytes[i];
        if b == b'}' || b == b']' {
            end = Some(i);
            break;
        }
    }
    let end = end?;
    let open_char = if bytes[end] == b'}' { b'{' } else { b'[' };
    let close_char = bytes[end];
    let mut depth = 0i32;
    let mut start: Option<usize> = None;
    for i in (0..=end).rev() {
        let b = bytes[i];
        if b == close_char {
            depth += 1;
        } else if b == open_char {
            depth -= 1;
            if depth == 0 {
                start = Some(i);
                break;
            }
        }
    }
    start.map(|s| String::from_utf8_lossy(&bytes[s..=end]).to_string())
}

/// 메서드명은 보수적으로 영문/숫자/점/하이픈/언더스코어만 허용 — 셸 인젝션 방어.
fn validate_method(method: &str) -> Result<(), OpenClawCliError> {
    if method.is_empty()
        || !method
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_'))
    {
        return Err(OpenClawCliError::SpawnFailed(format!(
            "잘못된 메서드명: {}",
            method
        )));
    }
    Ok(())
}

/// 사용자 입력 인자 — 셸 메타문자 차단. tokio::process는 인자 escape를 알아서 하지만
/// 메시지 본문은 따로 보호하지 않고(`-m` 다음에 그대로 들어감) 다른 옵션 값들만 보수적으로 검증.
fn validate_simple_arg(value: &str, label: &str) -> Result<(), OpenClawCliError> {
    if value.contains('\0') || value.contains('\n') {
        return Err(OpenClawCliError::SpawnFailed(format!(
            "{}에 제어문자 포함: {:?}",
            label, value
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn method_validator_accepts_typical_methods() {
        for ok in ["health", "system-presence", "cron.list", "agent.status"] {
            assert!(validate_method(ok).is_ok(), "거부됨: {}", ok);
        }
    }

    #[test]
    fn method_validator_rejects_shell_metachars() {
        for bad in [
            "health; rm -rf /",
            "health|nc",
            "h`whoami`",
            "h$(id)",
            "h\n",
        ] {
            assert!(validate_method(bad).is_err(), "통과됨: {:?}", bad);
        }
    }

    #[test]
    fn method_validator_rejects_empty() {
        assert!(validate_method("").is_err());
    }

    #[test]
    fn simple_arg_rejects_null_and_newline() {
        assert!(validate_simple_arg("ok-value", "x").is_ok());
        assert!(validate_simple_arg("bad\nvalue", "x").is_err());
        assert!(validate_simple_arg("nul\0byte", "x").is_err());
    }

    #[test]
    fn extracts_trailing_json_object() {
        let s = "[diagnostic] starting\n{\"ok\":true,\"ts\":1779206472986}";
        assert_eq!(
            extract_last_json_blob(s).as_deref(),
            Some("{\"ok\":true,\"ts\":1779206472986}")
        );
    }

    #[test]
    fn extracts_trailing_json_array() {
        let s = "log line\n[{\"a\":1},{\"a\":2}]";
        assert_eq!(
            extract_last_json_blob(s).as_deref(),
            Some("[{\"a\":1},{\"a\":2}]")
        );
    }

    #[test]
    fn extract_handles_nested_braces() {
        let s = "noise\n{\"outer\":{\"inner\":[1,2,3]},\"x\":true}";
        let got = extract_last_json_blob(s).unwrap();
        let parsed: Value = serde_json::from_str(&got).unwrap();
        assert_eq!(parsed["outer"]["inner"][2], 3);
    }
}
