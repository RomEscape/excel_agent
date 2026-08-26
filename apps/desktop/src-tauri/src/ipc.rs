use reqwest::Method;
use std::sync::Mutex;
use std::time::Duration;
use tauri::State;

use crate::sidecar::SidecarState;

/// Helper to build the sidecar URL.
fn sidecar_url(state: &SidecarState, path: &str) -> String {
    format!("http://127.0.0.1:{}{}", state.port, path)
}

/// Read a response and check its HTTP status code.
/// Returns `Err` for any non-2xx response, embedding the status code and body.
async fn read_response(resp: reqwest::Response) -> Result<String, String> {
    let status = resp.status();
    let body = resp
        .text()
        .await
        .map_err(|e| format!("응답 읽기 실패: {}", e))?;

    if status.is_success() {
        Ok(body)
    } else {
        Err(format!("HTTP {}: {}", status.as_u16(), body))
    }
}

/// 사이드카로의 순수 프록시 요청을 한 곳에 모은 헬퍼.
///
/// `method`/`path`/`body`/`timeout`과 에러 메시지 prefix를 받아
/// `bearer_auth` + (선택)`json(body)` + (선택)`timeout`을 적용해 요청을 보내고,
/// 응답은 `read_response`로 HTTP 상태를 검사한 뒤 본문 문자열을 반환한다.
///
/// 파일 다운로드/멀티파트/로컬 FS 작업 등 순수 프록시가 아닌 명령은 이 헬퍼를
/// 거치지 않고 직접 구현한다.
async fn sidecar_request(
    state: &State<'_, Mutex<SidecarState>>,
    method: Method,
    path: &str,
    body: Option<serde_json::Value>,
    timeout: Option<Duration>,
    err_prefix: &str,
) -> Result<String, String> {
    let (url, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (sidecar_url(&s, path), s.auth_token.clone())
    };

    let mut req = reqwest::Client::new()
        .request(method, &url)
        .bearer_auth(&token);
    if let Some(body) = body {
        req = req.json(&body);
    }
    if let Some(timeout) = timeout {
        req = req.timeout(timeout);
    }

    let resp = req
        .send()
        .await
        .map_err(|e| format!("{}: {}", err_prefix, e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn health_check(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(&state, Method::GET, "/health", None, None, "헬스 체크 실패").await
}

#[tauri::command]
pub async fn store_credential(
    state: State<'_, Mutex<SidecarState>>,
    service: String,
    value: String,
) -> Result<String, String> {
    let body = serde_json::json!({ "key": service, "value": value });
    sidecar_request(
        &state,
        Method::POST,
        "/credentials",
        Some(body),
        None,
        "자격증명 저장 실패",
    )
    .await
}

#[tauri::command]
pub async fn get_credential(
    state: State<'_, Mutex<SidecarState>>,
    service: String,
) -> Result<String, String> {
    let path = format!("/credentials/{}", service);
    sidecar_request(&state, Method::GET, &path, None, None, "자격증명 조회 실패").await
}

#[tauri::command]
pub async fn delete_credential(
    state: State<'_, Mutex<SidecarState>>,
    service: String,
) -> Result<String, String> {
    let path = format!("/credentials/{}", service);
    sidecar_request(
        &state,
        Method::DELETE,
        &path,
        None,
        None,
        "자격증명 삭제 실패",
    )
    .await
}

#[tauri::command]
pub async fn list_credentials(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::GET,
        "/credentials",
        None,
        None,
        "자격증명 목록 조회 실패",
    )
    .await
}

#[tauri::command]
pub async fn chat(
    state: State<'_, Mutex<SidecarState>>,
    message: String,
    model: Option<String>,
) -> Result<String, String> {
    let body = serde_json::json!({ "message": message, "model": model });
    sidecar_request(
        &state,
        Method::POST,
        "/llm/chat",
        Some(body),
        Some(Duration::from_secs(120)),
        "채팅 요청 실패",
    )
    .await
}

#[tauri::command]
pub async fn get_audit_logs(
    state: State<'_, Mutex<SidecarState>>,
    limit: Option<u32>,
) -> Result<String, String> {
    let limit_param = limit.map_or(String::new(), |l| format!("?limit={}", l));
    let path = format!("/audit/logs{}", limit_param);
    sidecar_request(
        &state,
        Method::GET,
        &path,
        None,
        None,
        "감사 로그 조회 실패",
    )
    .await
}

// ── Excel Live(COM) commands ────────────────────────────────────────────────

#[tauri::command]
pub async fn excel_live_status(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::GET,
        "/excel-live/status",
        None,
        None,
        "Excel Live 상태 조회 실패",
    )
    .await
}

#[tauri::command]
pub async fn excel_live_command(
    state: State<'_, Mutex<SidecarState>>,
    message: String,
    workbook_id: Option<String>,
    sheet_name: Option<String>,
    approve: Option<bool>,
    history: Option<serde_json::Value>,
) -> Result<String, String> {
    // history: [{role, content}] — 멀티턴 맥락. 없으면 빈 배열로 전달.
    let body = serde_json::json!({
        "message": message,
        "workbook_id": workbook_id,
        "sheet_name": sheet_name,
        "approve": approve.unwrap_or(false),
        "history": history.unwrap_or_else(|| serde_json::json!([])),
    });
    sidecar_request(
        &state,
        Method::POST,
        "/excel-live/command",
        Some(body),
        Some(Duration::from_secs(180)),
        "Excel Live 명령 실행 실패",
    )
    .await
}

#[tauri::command]
pub async fn excel_live_submit_approval(
    state: State<'_, Mutex<SidecarState>>,
    approval_id: String,
    approved: bool,
    rejection_reason: Option<String>,
) -> Result<String, String> {
    let body = serde_json::json!({
        "approval_id": approval_id,
        "approved": approved,
        "rejection_reason": rejection_reason,
    });
    sidecar_request(
        &state,
        Method::POST,
        "/excel-live/approval",
        Some(body),
        Some(Duration::from_secs(120)),
        "Excel Live 승인 응답 실패",
    )
    .await
}

#[tauri::command]
pub async fn excel_live_save_workbook(
    state: State<'_, Mutex<SidecarState>>,
    workbook_id: Option<String>,
) -> Result<String, String> {
    let body = serde_json::json!({
        "action": "excel_live.save_workbook",
        "params": {},
        "workbook_id": workbook_id,
        "sheet_name": serde_json::Value::Null,
        "approve": true,
    });
    sidecar_request(
        &state,
        Method::POST,
        "/excel-live/action",
        Some(body),
        Some(Duration::from_secs(30)),
        "Excel 저장 요청 실패",
    )
    .await
}

// ── Maintenance commands ─────────────────────────────────────────────────────

#[tauri::command]
pub async fn maintenance_cleanup(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::POST,
        "/maintenance/cleanup",
        None,
        None,
        "임시 파일 정리 요청 실패",
    )
    .await
}

// ── Ollama 로컬 LLM ─────────────────────────────────────────────────────────

/// Ollama 종합 상태 — installed, running, version, models 한 번에.
#[tauri::command]
pub async fn ollama_status() -> Result<String, String> {
    let info = crate::ollama::get_ollama_status().await;
    Ok(info.to_string())
}

// ── Phase 3 (2026-05): Rust keyring + audit ─────────────────────────────────
//
// Python의 KeyringService/AuditService와 *동일한* 저장소(OS keychain +
// JSONL 파일)를 공유한다. 기존 ipc::store_credential 등은 Python을 경유하지만
// 아래 rust_* 명령은 Rust가 직접 OS에 붙는다 — Python 경로의 가용성과 독립.

#[tauri::command]
pub fn rust_credential_set(
    app: tauri::AppHandle,
    key: String,
    value: String,
) -> Result<(), String> {
    crate::keyring_svc::store(&app, &key, &value)
}

#[tauri::command]
pub fn rust_credential_get(app: tauri::AppHandle, key: String) -> Result<Option<String>, String> {
    crate::keyring_svc::retrieve(&app, &key)
}

#[tauri::command]
pub fn rust_credential_delete(app: tauri::AppHandle, key: String) -> Result<(), String> {
    crate::keyring_svc::delete(&app, &key)
}

#[tauri::command]
pub fn rust_credential_list(app: tauri::AppHandle) -> Vec<String> {
    crate::keyring_svc::list_keys(&app)
}

#[tauri::command]
pub fn rust_audit_log(
    app: tauri::AppHandle,
    action: String,
    target: String,
    detail: Option<String>,
) {
    crate::audit::log(&app, &action, &target, detail.as_deref().unwrap_or(""));
}

#[tauri::command]
pub fn rust_audit_recent(
    app: tauri::AppHandle,
    limit: Option<u32>,
) -> Vec<crate::audit::AuditEntry> {
    crate::audit::get_logs(&app, limit.unwrap_or(100) as usize)
}

#[tauri::command]
pub fn rust_audit_masking_stats(app: tauri::AppHandle) -> crate::audit::MaskingStats {
    crate::audit::masking_stats(&app)
}

#[tauri::command]
pub fn rust_audit_blocked(
    app: tauri::AppHandle,
    limit: Option<u32>,
) -> Vec<crate::audit::AuditEntry> {
    crate::audit::get_blocked_log(&app, limit.unwrap_or(50) as usize)
}

#[tauri::command]
pub fn rust_audit_last_blocked_at(app: tauri::AppHandle) -> Option<String> {
    crate::audit::last_blocked_at(&app)
}

// ── Phase 5: Security Dashboard commands ─────────────────────────────────────

/// 마스킹/차단 통계를 반환한다.
#[tauri::command]
pub async fn security_stats(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::GET,
        "/security/stats",
        None,
        Some(Duration::from_secs(10)),
        "보안 통계 조회 실패",
    )
    .await
}

/// 최근 보안 차단 이벤트 목록을 반환한다.
#[tauri::command]
pub async fn security_blocked_log(
    state: State<'_, Mutex<SidecarState>>,
    limit: Option<u32>,
) -> Result<String, String> {
    let limit_param = limit.map_or(String::new(), |l| format!("?limit={}", l));
    let path = format!("/security/blocked-log{}", limit_param);
    sidecar_request(
        &state,
        Method::GET,
        &path,
        None,
        Some(Duration::from_secs(10)),
        "차단 이력 조회 실패",
    )
    .await
}

/// 현재 스킬별 권한 설정을 반환한다.
#[tauri::command]
pub async fn security_get_whitelist(
    state: State<'_, Mutex<SidecarState>>,
) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::GET,
        "/security/whitelist",
        None,
        Some(Duration::from_secs(10)),
        "화이트리스트 조회 실패",
    )
    .await
}

/// 스킬별 권한을 업데이트한다.
#[tauri::command]
pub async fn security_update_whitelist(
    state: State<'_, Mutex<SidecarState>>,
    overrides: serde_json::Value,
) -> Result<String, String> {
    let body = serde_json::json!({ "overrides": overrides });
    sidecar_request(
        &state,
        Method::PUT,
        "/security/whitelist",
        Some(body),
        Some(Duration::from_secs(10)),
        "화이트리스트 저장 실패",
    )
    .await
}

/// 현재 마스킹 설정(mask_email, mask_phone)을 반환한다.
#[tauri::command]
pub async fn security_get_masking_settings(
    state: State<'_, Mutex<SidecarState>>,
) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::GET,
        "/security/masking-settings",
        None,
        Some(Duration::from_secs(10)),
        "마스킹 설정 조회 실패",
    )
    .await
}

/// 마스킹 설정을 업데이트한다 (mask_email, mask_phone).
#[tauri::command]
pub async fn security_update_masking_settings(
    state: State<'_, Mutex<SidecarState>>,
    mask_email: bool,
    mask_phone: bool,
) -> Result<String, String> {
    let body = serde_json::json!({ "mask_email": mask_email, "mask_phone": mask_phone });
    sidecar_request(
        &state,
        Method::POST,
        "/security/masking-settings",
        Some(body),
        Some(Duration::from_secs(10)),
        "마스킹 설정 저장 실패",
    )
    .await
}

// ── Phase 3: Permissions commands ────────────────────────────────────────────

#[tauri::command]
pub async fn permissions_get(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::GET,
        "/permissions",
        None,
        Some(Duration::from_secs(10)),
        "권한 설정 조회 실패",
    )
    .await
}

#[tauri::command]
pub async fn permissions_update(
    state: State<'_, Mutex<SidecarState>>,
    allowed_folders: Vec<String>,
    allowed_apps: Vec<String>,
    shell_command_whitelist: Vec<String>,
    python_module_whitelist: Vec<String>,
) -> Result<String, String> {
    let body = serde_json::json!({
        "allowed_folders": allowed_folders,
        "allowed_apps": allowed_apps,
        "shell_command_whitelist": shell_command_whitelist,
        "python_module_whitelist": python_module_whitelist,
    });
    sidecar_request(
        &state,
        Method::PUT,
        "/permissions",
        Some(body),
        Some(Duration::from_secs(10)),
        "권한 설정 저장 실패",
    )
    .await
}

#[tauri::command(rename_all = "snake_case")]
pub async fn permissions_whitelist_add(
    state: State<'_, Mutex<SidecarState>>,
    command: String,
    command_type: String,
    reason: String,
) -> Result<String, String> {
    let body =
        serde_json::json!({ "command": command, "command_type": command_type, "reason": reason });
    sidecar_request(
        &state,
        Method::POST,
        "/permissions/whitelist",
        Some(body),
        Some(Duration::from_secs(10)),
        "화이트리스트 추가 실패",
    )
    .await
}

#[tauri::command]
pub async fn permissions_whitelist_remove(
    state: State<'_, Mutex<SidecarState>>,
    command: String,
) -> Result<String, String> {
    let path = format!("/permissions/whitelist/{}", urlencoding::encode(&command));
    sidecar_request(
        &state,
        Method::DELETE,
        &path,
        None,
        Some(Duration::from_secs(10)),
        "화이트리스트 제거 실패",
    )
    .await
}

// ── Phase 2: Command Audit Log commands ──────────────────────────────────────

/// 명령 감사 로그 목록을 반환한다.
#[tauri::command]
pub async fn command_audit_list(
    state: State<'_, Mutex<SidecarState>>,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<String, String> {
    let path = format!(
        "/security/audit?limit={}&offset={}",
        limit.unwrap_or(50),
        offset.unwrap_or(0)
    );
    sidecar_request(
        &state,
        Method::GET,
        &path,
        None,
        Some(Duration::from_secs(10)),
        "명령 감사 로그 조회 실패",
    )
    .await
}

/// 명령 감사 로그 등급별 통계를 반환한다.
#[tauri::command]
pub async fn command_audit_stats(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::GET,
        "/security/audit/stats",
        None,
        Some(Duration::from_secs(10)),
        "명령 감사 통계 조회 실패",
    )
    .await
}

/// 명령 감사 로그 전체를 초기화한다.
#[tauri::command]
pub async fn command_audit_clear(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::DELETE,
        "/security/audit",
        None,
        Some(Duration::from_secs(10)),
        "명령 감사 로그 초기화 실패",
    )
    .await
}

// ── Phase 1: officeclaw — Workspace commands ──────────────────────────────

/// 워크스페이스 폴더를 Finder(macOS) / Explorer(Windows)로 연다.
#[tauri::command]
pub async fn open_workspace_folder(_app: tauri::AppHandle) -> Result<String, String> {
    let workspace = workspace_dir().ok_or_else(|| "홈 디렉토리를 찾을 수 없습니다".to_string())?;

    // 디렉토리가 없으면 생성
    if !workspace.exists() {
        std::fs::create_dir_all(&workspace)
            .map_err(|e| format!("워크스페이스 폴더 생성 실패: {}", e))?;
    }

    let path_str = workspace.to_string_lossy().to_string();

    // macOS: open, Windows: explorer, Linux: xdg-open
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("open").arg(&path_str).status();
    #[cfg(target_os = "windows")]
    let result = std::process::Command::new("explorer")
        .arg(&path_str)
        .status();
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let result = std::process::Command::new("xdg-open")
        .arg(&path_str)
        .status();

    result.map_err(|e| format!("폴더 열기 실패: {}", e))?;

    Ok(path_str)
}

/// 워크스페이스 내 파일을 OS 기본 연결 앱으로 연다.
///
/// 보안:
///   - 절대 경로 차단 (상대 경로만 허용)
///   - `..` 포함 경로 차단 (워크스페이스 탈출 방지)
#[tauri::command]
pub async fn open_workspace_file(path: String) -> Result<String, String> {
    // 1) 입력 경로 검증
    if path.starts_with('/') || path.starts_with('\\') {
        return Err("절대 경로는 허용되지 않습니다. 상대 경로를 사용하세요.".to_string());
    }
    let candidate = std::path::Path::new(&path);
    for component in candidate.components() {
        if component == std::path::Component::ParentDir {
            return Err("경로에 '..'가 포함될 수 없습니다.".to_string());
        }
    }

    // 2) 워크스페이스 루트 확인/생성
    let workspace_root =
        workspace_dir().ok_or_else(|| "홈 디렉토리를 찾을 수 없습니다".to_string())?;
    if !workspace_root.exists() {
        std::fs::create_dir_all(&workspace_root)
            .map_err(|e| format!("워크스페이스 폴더 생성 실패: {}", e))?;
    }

    // 3) 대상 파일 경로 계산 + 경계 확인
    let target = workspace_root.join(&path);
    if !target.starts_with(&workspace_root) {
        return Err("워크스페이스 경계를 벗어나는 경로입니다.".to_string());
    }
    if !target.exists() {
        return Err(format!("파일을 찾을 수 없습니다: {}", path));
    }
    if target.is_dir() {
        return Err("디렉토리는 open_workspace_folder로 열어주세요.".to_string());
    }

    let target_str = target.to_string_lossy().to_string();

    // 4) OS 기본 앱으로 열기
    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("open").arg(&target_str).status();
    #[cfg(target_os = "windows")]
    let result = std::process::Command::new("powershell")
        .args([
            "-NoProfile",
            "-Command",
            "Start-Process",
            "-FilePath",
            &target_str,
        ])
        .status();
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let result = std::process::Command::new("xdg-open")
        .arg(&target_str)
        .status();

    result.map_err(|e| format!("파일 열기 실패: {}", e))?;

    let response = serde_json::json!({
        "ok": true,
        "path": path,
        "absolute_path": target_str,
    });
    Ok(response.to_string())
}

/// 워크스페이스 파일 목록을 반환한다.
#[tauri::command]
pub async fn workspace_list_files(
    state: State<'_, Mutex<SidecarState>>,
    path: Option<String>,
) -> Result<String, String> {
    let path_param = path
        .as_deref()
        .filter(|p| !p.is_empty())
        .map(|p| format!("?path={}", urlencoding::encode(p)))
        .unwrap_or_default();
    let endpoint = format!("/workspace/files{}", path_param);
    sidecar_request(
        &state,
        Method::GET,
        &endpoint,
        None,
        Some(Duration::from_secs(10)),
        "워크스페이스 파일 목록 조회 실패",
    )
    .await
}

/// 워크스페이스 내 파일 내용을 읽는다.
#[tauri::command]
pub async fn workspace_read_file(
    state: State<'_, Mutex<SidecarState>>,
    path: String,
) -> Result<String, String> {
    let endpoint = format!("/workspace/file?path={}", urlencoding::encode(&path));
    sidecar_request(
        &state,
        Method::GET,
        &endpoint,
        None,
        Some(Duration::from_secs(10)),
        "파일 읽기 실패",
    )
    .await
}

/// 워크스페이스 내 파일 하나를 삭제한다. 홈 화면의 `문서 삭제`가 쓴다.
#[tauri::command]
pub async fn workspace_delete_file(
    state: State<'_, Mutex<SidecarState>>,
    path: String,
) -> Result<String, String> {
    let endpoint = format!("/workspace/file?path={}", urlencoding::encode(&path));
    sidecar_request(
        &state,
        Method::DELETE,
        &endpoint,
        None,
        Some(Duration::from_secs(10)),
        "파일 삭제 실패",
    )
    .await
}

/// 워크스페이스 내 파일에 내용을 쓴다.
#[tauri::command]
pub async fn workspace_write_file(
    state: State<'_, Mutex<SidecarState>>,
    path: String,
    content: String,
) -> Result<String, String> {
    let body = serde_json::json!({ "path": path, "content": content });
    sidecar_request(
        &state,
        Method::POST,
        "/workspace/file",
        Some(body),
        Some(Duration::from_secs(10)),
        "파일 쓰기 실패",
    )
    .await
}

/// 워크스페이스에 새 엑셀(.xlsx) 파일을 생성한다.
#[tauri::command(rename_all = "snake_case")]
pub async fn workspace_create_excel_file(
    state: State<'_, Mutex<SidecarState>>,
    path: String,
    sheet_name: Option<String>,
) -> Result<String, String> {
    let body = serde_json::json!({ "path": path, "sheet_name": sheet_name });
    sidecar_request(
        &state,
        Method::POST,
        "/workspace/excel-file",
        Some(body),
        Some(Duration::from_secs(10)),
        "엑셀 파일 생성 실패",
    )
    .await
}

/// 워크스페이스에 base64 인코딩된 바이너리 파일을 쓴다.
///
/// 보안 샌드박스:
///   - 경로 내 `..` 컴포넌트 차단 (디렉토리 탈출 방지)
///   - 절대 경로 차단 — 반드시 상대 경로 사용
///   - 최종 경로는 ~/officeclaw/Workspace/{path} 고정
///
/// 호출 측(Frontend)은 base64 표준 인코딩(RFC 4648) 문자열을 전달해야 한다.
///
/// NOTE: Tauri v2는 명령 인자 키를 기본 camelCase로 역직렬화한다. 이 명령은
/// State를 받지 않고 인자를 Tauri가 직접 역직렬화하므로, api.js가 보내는
/// snake_case(`content_base64`)를 받으려면 `rename_all = "snake_case"`가 필요하다.
/// (없으면 `contentBase64`를 기대해 "missing required key contentBase64"로 실패.)
#[tauri::command(rename_all = "snake_case")]
pub async fn workspace_write_file_binary(
    path: String,
    content_base64: String,
) -> Result<String, String> {
    // 1. 샌드박스 검증 — 절대 경로 거부
    if path.starts_with('/') || path.starts_with('\\') {
        return Err("절대 경로는 허용되지 않습니다. 상대 경로를 사용하세요.".to_string());
    }

    // 2. 샌드박스 검증 — '..' 컴포넌트 거부
    let candidate = std::path::Path::new(&path);
    for component in candidate.components() {
        if component == std::path::Component::ParentDir {
            return Err("경로에 '..'가 포함될 수 없습니다.".to_string());
        }
    }

    // 3. 워크스페이스 루트 확인 및 생성
    let workspace_root =
        workspace_dir().ok_or_else(|| "홈 디렉토리를 찾을 수 없습니다".to_string())?;
    if !workspace_root.exists() {
        std::fs::create_dir_all(&workspace_root)
            .map_err(|e| format!("워크스페이스 폴더 생성 실패: {}", e))?;
    }

    // 4. 최종 경로 계산 (정규화 후 루트 이탈 재확인)
    let target = workspace_root.join(&path);
    if !target.starts_with(&workspace_root) {
        return Err("워크스페이스 경계를 벗어나는 경로입니다.".to_string());
    }

    // 5. 부모 디렉토리 생성
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("디렉토리 생성 실패: {}", e))?;
    }

    // 6. base64 디코드
    use base64::Engine as _;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(content_base64.trim())
        .map_err(|e| format!("base64 디코딩 실패: {}", e))?;

    // 7. 바이너리 쓰기
    std::fs::write(&target, &bytes).map_err(|e| format!("파일 쓰기 실패: {}", e))?;

    let saved_path = target.to_string_lossy().to_string();
    let response = serde_json::json!({
        "ok": true,
        "path": path,
        "absolute_path": saved_path,
        "size_bytes": bytes.len(),
    });
    Ok(response.to_string())
}

// ── Sprint 5: 자동 업데이트 (Tauri Updater Plugin) ───────────────────────────

/// 업데이트 가능 여부를 확인한다.
/// 새 버전이 있으면 {available: true, version, notes} 반환, 없으면 {available: false}.
/// updater가 미설정(pubkey 없음) 상태에서는 {available: false, error: ...} 반환.
#[tauri::command]
pub async fn check_for_update(app: tauri::AppHandle) -> Result<String, String> {
    use tauri_plugin_updater::UpdaterExt;

    let updater = app
        .updater_builder()
        .build()
        .map_err(|e| format!("updater 초기화 실패: {}", e))?;

    match updater.check().await {
        Ok(Some(update)) => {
            let result = serde_json::json!({
                "available": true,
                "version": update.version,
                "current_version": update.current_version,
                "notes": update.body,
            });
            Ok(result.to_string())
        }
        Ok(None) => {
            let result = serde_json::json!({ "available": false });
            Ok(result.to_string())
        }
        Err(e) => {
            // 업데이트 서버 미설정 시 조용히 처리 (개발 환경)
            let result = serde_json::json!({
                "available": false,
                "error": e.to_string(),
            });
            Ok(result.to_string())
        }
    }
}

// ── Sprint 5: 채팅 세션 영속화 ────────────────────────────────────────────────

/// 채팅 메시지를 영속 저장한다.
#[tauri::command(rename_all = "snake_case")]
#[allow(clippy::too_many_arguments)] // Tauri IPC 명령은 입력을 평면 인자로 받는 게 관례
pub async fn chat_save_message(
    state: State<'_, Mutex<SidecarState>>,
    session_id: String,
    role: String,
    text: Option<String>,
    tool_calls: Option<serde_json::Value>,
    masked_count: Option<u32>,
    masked_types: Option<serde_json::Value>,
    error_text: Option<String>,
) -> Result<String, String> {
    let body = serde_json::json!({
        "session_id": session_id,
        "role": role,
        "text": text,
        "tool_calls": tool_calls,
        "masked_count": masked_count.unwrap_or(0),
        "masked_types": masked_types,
        "error_text": error_text,
    });
    sidecar_request(
        &state,
        Method::POST,
        "/chat/messages",
        Some(body),
        Some(Duration::from_secs(10)),
        "채팅 메시지 저장 실패",
    )
    .await
}

/// 채팅 세션 목록을 최근 활동순으로 반환한다.
#[tauri::command]
pub async fn chat_list_sessions(
    state: State<'_, Mutex<SidecarState>>,
    limit: Option<u32>,
) -> Result<String, String> {
    let limit_param = limit.map_or(String::new(), |l| format!("?limit={}", l));
    let endpoint = format!("/chat/sessions{}", limit_param);
    sidecar_request(
        &state,
        Method::GET,
        &endpoint,
        None,
        Some(Duration::from_secs(10)),
        "채팅 세션 목록 조회 실패",
    )
    .await
}

/// 세션의 전체 메시지를 반환한다.
#[tauri::command(rename_all = "snake_case")]
pub async fn chat_get_messages(
    state: State<'_, Mutex<SidecarState>>,
    session_id: String,
) -> Result<String, String> {
    let endpoint = format!(
        "/chat/sessions/{}/messages",
        urlencoding::encode(&session_id)
    );
    sidecar_request(
        &state,
        Method::GET,
        &endpoint,
        None,
        Some(Duration::from_secs(10)),
        "채팅 메시지 조회 실패",
    )
    .await
}

/// 세션과 하위 메시지를 모두 삭제한다.
#[tauri::command(rename_all = "snake_case")]
pub async fn chat_delete_session(
    state: State<'_, Mutex<SidecarState>>,
    session_id: String,
) -> Result<String, String> {
    let endpoint = format!("/chat/sessions/{}", urlencoding::encode(&session_id));
    sidecar_request(
        &state,
        Method::DELETE,
        &endpoint,
        None,
        Some(Duration::from_secs(10)),
        "채팅 세션 삭제 실패",
    )
    .await
}

// ── Sprint 5: 백업/내보내기 ───────────────────────────────────────────────────

/// 현재 데이터를 ~/Downloads/ajou-ai-backup-{timestamp}.zip으로 내보낸다.
#[tauri::command]
pub async fn backup_export(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::POST,
        "/backup/export",
        None,
        Some(Duration::from_secs(60)),
        "백업 export 실패",
    )
    .await
}

/// 지정된 zip 파일로부터 데이터를 복원한다.
#[tauri::command(rename_all = "snake_case")]
pub async fn backup_import(
    state: State<'_, Mutex<SidecarState>>,
    file_path: String,
) -> Result<String, String> {
    let body = serde_json::json!({ "file_path": file_path });
    sidecar_request(
        &state,
        Method::POST,
        "/backup/import",
        Some(body),
        Some(Duration::from_secs(60)),
        "백업 import 실패",
    )
    .await
}

// ── Private helpers ─────────────────────────────────────────────────────────

fn dirs_home() -> Option<std::path::PathBuf> {
    std::env::var_os("HOME")
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("USERPROFILE").map(std::path::PathBuf::from))
}

/// 워크스페이스 루트 (~/officeclaw/Workspace). 경로 단일 출처.
/// Python sandbox(config.get_workspace_root)와 동일한 위치를 가리킨다.
fn workspace_dir() -> Option<std::path::PathBuf> {
    dirs_home().map(|home| home.join("officeclaw").join("Workspace"))
}

#[tauri::command]
pub async fn get_llm_settings(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::GET,
        "/settings/llm",
        None,
        None,
        "LLM 설정 조회 실패",
    )
    .await
}

#[tauri::command]
pub async fn save_llm_settings(
    state: State<'_, Mutex<SidecarState>>,
    config: serde_json::Value,
) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::POST,
        "/settings/llm",
        Some(config),
        None,
        "LLM 설정 저장 실패",
    )
    .await
}

// ── 모바일 릴레이(중계 서버) 연동 — QR 페어링 ────────────────────────────────

/// relay에 페어링을 개시하고 {pairing_id, code, relay_url}을 받는다 (QR 렌더용).
///
/// `relay_url`을 주면 사이드카 config가 그 주소로 갱신된다(실기기 테스트 시 LAN IP 등).
#[tauri::command(rename_all = "snake_case")]
pub async fn relay_pair(
    state: State<'_, Mutex<SidecarState>>,
    relay_url: Option<String>,
) -> Result<String, String> {
    let body = serde_json::json!({ "relay_url": relay_url });
    sidecar_request(
        &state,
        Method::POST,
        "/relay/pair",
        Some(body),
        // 사이드카가 relay 서버로 나가는 왕복이 있어 여유를 둔다
        Some(Duration::from_secs(15)),
        "릴레이 페어링 개시 실패",
    )
    .await
}

/// 릴레이 연동 상태 {enabled, relay_url, pairing_id, connected}.
#[tauri::command]
pub async fn relay_status(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::GET,
        "/relay/status",
        None,
        None,
        "릴레이 상태 조회 실패",
    )
    .await
}

/// 릴레이 연동 중지(enabled=false) + 백그라운드 클라이언트 정리.
#[tauri::command]
pub async fn relay_disconnect(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    sidecar_request(
        &state,
        Method::POST,
        "/relay/disconnect",
        None,
        None,
        "릴레이 연결 해제 실패",
    )
    .await
}
