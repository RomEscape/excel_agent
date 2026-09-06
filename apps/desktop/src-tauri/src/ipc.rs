use reqwest::Method;
use std::sync::Mutex;
use std::time::Duration;
use tauri::State;

use crate::openclaw::OpenClawState;
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
#[allow(clippy::too_many_arguments)] // 사이드카 요청 계약과 1:1 — 묶으면 JS 쪽 키 매핑이 또 어긋난다
pub async fn excel_live_command(
    state: State<'_, Mutex<SidecarState>>,
    message: String,
    workbook_id: Option<String>,
    sheet_name: Option<String>,
    session_id: Option<String>,
    approve: Option<bool>,
    context_range: Option<String>,
    client: Option<serde_json::Value>,
) -> Result<String, String> {
    // 리베이스 후 JS 시그니처(sessionId·contextRange·clientContext)와 이 명령이
    // 어긋나 세션이 파편화되고 붙여넣기 문맥이 통째로 유실됐다(2026-09-01 실측:
    // "여기에 입력해줘"가 위치 문맥 없이 도착). 사이드카 요청 모델과 1:1로 맞춘다.
    let body = serde_json::json!({
        "message": message,
        "workbook_id": workbook_id,
        "sheet_name": sheet_name,
        "session_id": session_id,
        "context_range": context_range,
        "approve": approve.unwrap_or(false),
        "client": client,
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

/// 워크스페이스 목록에서 클릭한 통합문서를 에이전트의 대상으로 잡는다.
///
/// 2026-09-06: 파일 엔진에는 대상을 고르는 경로가 없어 "가장 최근 수정된 파일"이 늘 대상이었다.
/// `workbook_id` 는 사이드카가 절대경로·상대경로·파일명 모두 받는다.
#[tauri::command]
pub async fn excel_live_select_workbook(
    state: State<'_, Mutex<SidecarState>>,
    workbook_id: String,
) -> Result<String, String> {
    let body = serde_json::json!({ "workbook_id": workbook_id });
    sidecar_request(
        &state,
        Method::POST,
        "/excel-live/select-workbook",
        Some(body),
        Some(Duration::from_secs(10)),
        "대상 통합문서 선택 실패",
    )
    .await
}

/// 앱 안 미리보기용 통합문서 스냅샷(값만). 2026-09-06 "화면 안에서 엑셀 파일 확인".
#[tauri::command]
pub async fn excel_live_preview(
    state: State<'_, Mutex<SidecarState>>,
    workbook_id: Option<String>,
    sheet_name: Option<String>,
    max_rows: Option<u32>,
    max_cols: Option<u32>,
) -> Result<String, String> {
    let mut query: Vec<String> = Vec::new();
    if let Some(v) = workbook_id.filter(|s| !s.trim().is_empty()) {
        query.push(format!("workbook_id={}", urlencoding::encode(&v)));
    }
    if let Some(v) = sheet_name.filter(|s| !s.trim().is_empty()) {
        query.push(format!("sheet_name={}", urlencoding::encode(&v)));
    }
    if let Some(v) = max_rows {
        query.push(format!("max_rows={}", v));
    }
    if let Some(v) = max_cols {
        query.push(format!("max_cols={}", v));
    }
    let path = if query.is_empty() {
        "/excel-live/preview".to_string()
    } else {
        format!("/excel-live/preview?{}", query.join("&"))
    };
    sidecar_request(
        &state,
        Method::GET,
        &path,
        None,
        Some(Duration::from_secs(20)),
        "통합문서 미리보기 조회 실패",
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
pub async fn open_workspace_folder(
    _app: tauri::AppHandle,
    state: State<'_, Mutex<SidecarState>>,
) -> Result<String, String> {
    let workspace = match sidecar_workspace_root(&state).await {
        Some(root) => root,
        None => workspace_dir().ok_or_else(|| "홈 디렉토리를 찾을 수 없습니다".to_string())?,
    };

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
pub async fn open_workspace_file(
    path: String,
    state: State<'_, Mutex<SidecarState>>,
) -> Result<String, String> {
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

    // 2) 워크스페이스 루트 확인/생성 — 목록과 같은 루트(사이드카 소유)를 쓴다
    let workspace_root = match sidecar_workspace_root(&state).await {
        Some(root) => root,
        None => workspace_dir().ok_or_else(|| "홈 디렉토리를 찾을 수 없습니다".to_string())?,
    };
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
    // Windows: 예전엔 `powershell -Command Start-Process -FilePath <경로>` 였는데, -Command 뒤 인자를
    // PowerShell 이 공백으로 다시 쪼개서 `C:\...\바탕 화면\...` 처럼 공백이 든 경로는 항상 실패했다
    // (2026-09-06 실측: 종료코드 1, Excel 안 뜸. 개발기 경로엔 공백이 없어 안 드러났다).
    // 게다가 종료코드를 안 봐서 실패가 `ok:true` 로 보고됐다. rundll32 는 argv 를 그대로 받는다.
    #[cfg(target_os = "windows")]
    let result = std::process::Command::new("rundll32.exe")
        .args(["url.dll,FileProtocolHandler", &target_str])
        .status();
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let result = std::process::Command::new("xdg-open")
        .arg(&target_str)
        .status();

    let status = result.map_err(|e| format!("파일 열기 실패: {}", e))?;
    if !status.success() {
        return Err(format!(
            "파일 열기 실패 (종료코드 {}): {}",
            status.code().unwrap_or(-1),
            path
        ));
    }

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
    state: State<'_, Mutex<SidecarState>>,
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

    // 3. 워크스페이스 루트 확인 및 생성 — 목록과 같은 루트(사이드카 소유)
    let workspace_root = match sidecar_workspace_root(&state).await {
        Some(root) => root,
        None => workspace_dir().ok_or_else(|| "홈 디렉토리를 찾을 수 없습니다".to_string())?,
    };
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

/// 사이드카가 소유한 워크스페이스 루트(/workspace/files의 `workspace`).
///
/// 목록·열기·업로드가 **같은 폴더**를 봐야 한다 — Rust가 옛 경로
/// (~/officeclaw/Workspace)를 따로 하드코딩해, 목록에 멀쩡히 보이는 파일이
/// "찾을 수 없습니다"로 죽고 업로드는 목록에 안 나타났다(2026-09-01 실측).
/// 사이드카가 아직 안 떠 있으면 옛 경로로 폴백한다.
async fn sidecar_workspace_root(
    state: &State<'_, Mutex<SidecarState>>,
) -> Option<std::path::PathBuf> {
    let raw = sidecar_request(
        state,
        Method::GET,
        "/workspace/files",
        None,
        Some(Duration::from_secs(5)),
        "워크스페이스 루트 조회 실패",
    )
    .await
    .ok()?;
    let value: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let root = value.get("workspace")?.as_str()?.trim().to_string();
    if root.is_empty() {
        None
    } else {
        Some(std::path::PathBuf::from(root))
    }
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

// ── 데모 브랜치 헬퍼 ──

/// Parse a Content-Disposition header value robustly.
///
/// Handles both the simple `filename="foo.ext"` form and the RFC 6266
/// `filename*=UTF-8''...` percent-encoded form.  Returns `None` if the
/// header is absent or unparseable.
fn parse_content_disposition_filename(header_value: &str) -> Option<String> {
    // Prefer the RFC 6266 extended form (filename*=UTF-8''...)
    for part in header_value.split(';') {
        let part = part.trim();
        if let Some(rest) = part.strip_prefix("filename*=") {
            // e.g. UTF-8''%ED%95%9C%EA%B8%80.xlsx
            let rest = rest.trim().trim_matches('"');
            if let Some(encoded) = rest.splitn(3, '\'').nth(2) {
                if let Ok(decoded) = urlencoding::decode(encoded) {
                    let name = decoded.trim().to_string();
                    if !name.is_empty() {
                        return Some(name);
                    }
                }
            }
        }
    }
    // Fall back to plain filename="..."
    for part in header_value.split(';') {
        let part = part.trim();
        if let Some(rest) = part.strip_prefix("filename=") {
            let name = rest.trim().trim_matches('"').to_string();
            if !name.is_empty() {
                return Some(name);
            }
        }
    }
    None
}

/// Return the user's Downloads directory, falling back to the home directory.
fn downloads_dir() -> Result<std::path::PathBuf, String> {
    let home = dirs_home().ok_or_else(|| "홈 디렉토리를 찾을 수 없습니다".to_string())?;
    let downloads = home.join("Downloads");
    if downloads.exists() {
        Ok(downloads)
    } else {
        Ok(home)
    }
}

/// Excel COM 큐를 타는 요청의 상한.
///
/// 사이드카는 COM 호출 하나를 `EXCEL_LIVE_QUEUE_TIMEOUT_SECONDS`(기본 180초)까지
/// 기다린다. 여기가 그보다 짧거나 같으면, 안쪽이 아직 정리 중인데 바깥이 먼저 끊는다.
/// 그러면 프론트는 실패를 보고 사용자는 편집이 안 됐다고 생각하는데 실제로는 반영된다.
///
/// 이 값을 바꿀 때는 `src/lib/requestPolicy.js`의 `IPC_CEILING_MS`도 같이 바꿔야 한다.
/// 프론트는 여기보다 더 오래 기다려야 Rust의 오류 메시지가 그대로 올라온다.
const EXCEL_QUEUE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(200);

/// Helper to create a client with auth.
fn client_with_auth(state: &SidecarState) -> (reqwest::Client, String) {
    (reqwest::Client::new(), state.auth_token.clone())
}

/// Download a binary file response, extract the filename from Content-Disposition,
/// save to the user's Downloads directory, and return the saved path.
///
/// Shared by `excel_export`, `document_export_docx`, and `document_export_pdf`.
async fn download_file_response(
    resp: reqwest::Response,
    fallback_filename: &str,
) -> Result<String, String> {
    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("HTTP {}: {}", status, body));
    }

    let filename = resp
        .headers()
        .get("content-disposition")
        .and_then(|v| v.to_str().ok())
        .and_then(parse_content_disposition_filename)
        .unwrap_or_else(|| fallback_filename.to_string());

    let bytes = resp
        .bytes()
        .await
        .map_err(|e| format!("응답 읽기 실패: {}", e))?;

    let save_path = downloads_dir()?.join(&filename);
    std::fs::write(&save_path, &bytes).map_err(|e| format!("파일 저장 실패: {}", e))?;

    Ok(save_path.to_string_lossy().to_string())
}

/// Minimal percent-encoding for query parameter values (encodes space, &, =, etc.)
fn urlencoding_simple(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for byte in s.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{:02X}", byte)),
        }
    }
    out
}

// ═══ 이하: 데모 브랜치 명령(2026-08-29 병합 이식) — 파일 분석·라이브 매크로·백업·하네스·문서·에이전트·OpenClaw·스킬 ═══

#[tauri::command]
pub async fn excel_upload(
    state: State<'_, Mutex<SidecarState>>,
    file_path: String,
) -> Result<String, String> {
    let (url, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (sidecar_url(&s, "/excel/upload"), s.auth_token.clone())
    };

    const MAX_UPLOAD_BYTES: u64 = 50 * 1024 * 1024; // 50 MB

    let path = std::path::Path::new(&file_path);

    // Pre-flight size check before reading the file into memory
    let file_size = std::fs::metadata(path)
        .map_err(|e| format!("파일 정보 읽기 실패: {}", e))?
        .len();
    if file_size > MAX_UPLOAD_BYTES {
        return Err("파일 크기가 50MB를 초과합니다. 더 작은 파일을 사용해 주세요.".to_string());
    }

    let file_bytes = std::fs::read(path).map_err(|e| format!("파일 읽기 실패: {}", e))?;
    let filename = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("upload.xlsx")
        .to_string();

    let part = reqwest::multipart::Part::bytes(file_bytes).file_name(filename);
    let form = reqwest::multipart::Form::new().part("file", part);

    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .multipart(form)
        .send()
        .await
        .map_err(|e| format!("Excel 업로드 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn excel_analyze(
    state: State<'_, Mutex<SidecarState>>,
    file_id: String,
    question: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel/analyze"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "file_id": file_id, "question": question });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("Excel 분석 요청 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn excel_report(
    state: State<'_, Mutex<SidecarState>>,
    file_id: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel/report"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "file_id": file_id });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("리포트 생성 요청 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn excel_formulas(
    state: State<'_, Mutex<SidecarState>>,
    file_id: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel/formulas"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "file_id": file_id });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(120))
        .send()
        .await
        .map_err(|e| format!("수식 제안 요청 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn excel_chart_data(
    state: State<'_, Mutex<SidecarState>>,
    file_id: String,
    sheet_name: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        let path = format!(
            "/excel/chart-data?file_id={}&sheet_name={}",
            urlencoding_simple(&file_id),
            urlencoding_simple(&sheet_name)
        );
        (
            sidecar_url(&s, &path),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("차트 데이터 요청 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn excel_export(
    state: State<'_, Mutex<SidecarState>>,
    file_id: String,
    report_markdown: String,
) -> Result<String, String> {
    let (url, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (sidecar_url(&s, "/excel/export"), s.auth_token.clone())
    };

    let body = serde_json::json!({
        "file_id": file_id,
        "report_markdown": report_markdown,
    });

    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Excel 내보내기 요청 실패: {}", e))?;

    download_file_response(resp, "AI_report.xlsx").await
}

// 파이프라인(LLM 경유 가능)을 타다 느려지거나 죽던 문제의 전용 경로다.
#[tauri::command]
pub async fn excel_live_selection(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel-live/selection"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Excel 선택 영역 조회 실패: {}", e))?;

    read_response(resp).await
}

// 현재 Excel 선택 영역 주소만 빠르게 조회한다. 붙여넣기 프로브가 전체 명령
/// 프론트에서 벌어진 사건(라우팅 결정·붙여넣기 프로브·화면 오류·타임아웃)을 사이드카의
/// chat_log.jsonl에 같은 형식으로 남긴다. 실패해도 앱 동작에는 영향이 없어야 하므로
/// 오류는 문자열로만 돌려준다(호출부는 무시한다).
#[tauri::command]
pub async fn trace_client_event(
    state: State<'_, Mutex<SidecarState>>,
    event: serde_json::Value,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/trace/client-event"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&event)
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .map_err(|e| format!("클라이언트 이벤트 기록 실패: {}", e))?;
    read_response(resp).await
}

/// 승인된 매크로를 한 단계 진행한다.
///
/// 전체를 한 번에 돌리지 않고 프론트가 한 걸음씩 당기는 구조라, 타임아웃은 명령 하나
/// 기준으로 잡는다.
#[tauri::command]
pub async fn excel_live_macro_step(
    state: State<'_, Mutex<SidecarState>>,
    macro_id: String,
    skip_indices: Option<Vec<i64>>,
    answer: Option<String>,
    skip_current: Option<bool>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel-live/macro/step"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "macro_id": macro_id,
        "skip_indices": skip_indices.unwrap_or_default(),
        "answer": answer,
        "skip_current": skip_current.unwrap_or(false),
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(EXCEL_QUEUE_TIMEOUT)
        .send()
        .await
        .map_err(|e| format!("매크로 단계 실행 실패: {}", e))?;

    read_response(resp).await
}

/// 매크로를 중단한다. rollback이면 매크로 시작 시점 백업으로 되돌린다.
#[tauri::command]
pub async fn excel_live_macro_abort(
    state: State<'_, Mutex<SidecarState>>,
    macro_id: String,
    rollback: Option<bool>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel-live/macro/abort"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "macro_id": macro_id,
        "rollback": rollback.unwrap_or(false),
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(EXCEL_QUEUE_TIMEOUT)
        .send()
        .await
        .map_err(|e| format!("매크로 중단 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn excel_live_list_backups(
    state: State<'_, Mutex<SidecarState>>,
    workbook_id: Option<String>,
    limit: Option<i32>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel-live/backups"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let mut req = client.get(&url).bearer_auth(&token);
    let mut query: Vec<(&str, String)> = Vec::new();
    if let Some(wb) = workbook_id.as_ref() {
        if !wb.trim().is_empty() {
            query.push(("workbook_id", wb.clone()));
        }
    }
    if let Some(n) = limit {
        if n > 0 {
            query.push(("limit", n.to_string()));
        }
    }
    if !query.is_empty() {
        req = req.query(&query);
    }

    let resp = req
        .send()
        .await
        .map_err(|e| format!("Excel 백업 목록 조회 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn excel_live_restore_last_backup(
    state: State<'_, Mutex<SidecarState>>,
    workbook_id: Option<String>,
    backup_path: Option<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel-live/restore-last"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "workbook_id": workbook_id,
        "backup_path": backup_path,
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(EXCEL_QUEUE_TIMEOUT)
        .send()
        .await
        .map_err(|e| format!("Excel 마지막 변경 복구 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
#[allow(clippy::too_many_arguments)] // Tauri IPC 명령은 입력을 평면 인자로 받는 게 관례
pub async fn harness_feedback(
    state: State<'_, Mutex<SidecarState>>,
    user_id: Option<String>,
    session_id: Option<String>,
    route: Option<String>,
    message: Option<String>,
    rating: String,
    reason: Option<String>,
    expected_action: Option<String>,
    expected_behavior: Option<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/harness/feedback"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "user_id": user_id,
        "session_id": session_id,
        "route": route.unwrap_or_else(|| "/excel-live/command".to_string()),
        "message": message.unwrap_or_default(),
        "rating": rating,
        "reason": reason.unwrap_or_default(),
        "expected_action": expected_action.unwrap_or_default(),
        "expected_behavior": expected_behavior.unwrap_or_default(),
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
        .map_err(|e| format!("하네스 피드백 저장 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
#[allow(clippy::too_many_arguments)] // Tauri IPC 명령은 입력을 평면 인자로 받는 게 관례
pub async fn harness_replay_failures(
    state: State<'_, Mutex<SidecarState>>,
    user_id: Option<String>,
    session_id: Option<String>,
    route: Option<String>,
    limit: Option<i32>,
    parse_timeout_seconds: Option<f64>,
    min_gate_cases: Option<i32>,
    min_gate_pass_rate: Option<f64>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/harness/replay-failures"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "user_id": user_id,
        "session_id": session_id,
        "route": route.unwrap_or_else(|| "/excel-live/command".to_string()),
        "limit": limit.unwrap_or(20),
        "parse_timeout_seconds": parse_timeout_seconds.unwrap_or(10.0),
        "min_gate_cases": min_gate_cases.unwrap_or(5),
        "min_gate_pass_rate": min_gate_pass_rate.unwrap_or(0.7),
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("하네스 실패 리플레이 실행 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn harness_personalization(
    state: State<'_, Mutex<SidecarState>>,
    user_id: Option<String>,
    session_id: Option<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/harness/personalization"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let mut req = client.get(&url).bearer_auth(&token);
    let mut query: Vec<(&str, String)> = Vec::new();
    if let Some(user) = user_id {
        if !user.trim().is_empty() {
            query.push(("user_id", user));
        }
    }
    if let Some(session) = session_id {
        if !session.trim().is_empty() {
            query.push(("session_id", session));
        }
    }
    if !query.is_empty() {
        req = req.query(&query);
    }

    let resp = req
        .send()
        .await
        .map_err(|e| format!("하네스 개인화 조회 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn document_generate(
    state: State<'_, Mutex<SidecarState>>,
    doc_type: String,
    content: String,
    tone: String,
    length: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/document/generate"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "doc_type": doc_type,
        "content": content,
        "tone": tone,
        "length": length,
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("문서 생성 요청 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn document_export_docx(
    state: State<'_, Mutex<SidecarState>>,
    title: String,
    content: String,
) -> Result<String, String> {
    let (url, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/document/export/docx"),
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "title": title, "content": content });

    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Word 내보내기 요청 실패: {}", e))?;

    let fallback = format!("{}.docx", title);
    download_file_response(resp, &fallback).await
}

#[tauri::command]
pub async fn document_export_pdf(
    state: State<'_, Mutex<SidecarState>>,
    title: String,
    content: String,
) -> Result<String, String> {
    let (url, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/document/export/pdf"),
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "title": title, "content": content });

    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("PDF 내보내기 요청 실패: {}", e))?;

    let fallback = format!("{}.pdf", title);
    download_file_response(resp, &fallback).await
}

/// OpenClaw 세션을 통해 메시지를 전송한다.
/// Python 보안 레이어를 경유: /agent/chat
///
/// session_id가 None이면 새 세션을 생성한다.
#[tauri::command]
pub async fn agent_chat(
    state: State<'_, Mutex<SidecarState>>,
    message: String,
    session_id: Option<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/agent/chat"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "message": message,
        "session_id": session_id,
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("Agent 채팅 요청 실패: {}", e))?;

    read_response(resp).await
}

/// 활성 OpenClaw 세션 목록 조회.
#[tauri::command]
pub async fn agent_sessions(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/agent/sessions"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("세션 목록 조회 실패: {}", e))?;

    read_response(resp).await
}

/// OpenClaw 게이트웨이 현재 상태 확인.
/// React UI에서 연결 상태 배지를 렌더링하는 데 사용.
#[tauri::command]
pub async fn openclaw_status(oc_state: State<'_, Mutex<OpenClawState>>) -> Result<String, String> {
    let status = crate::openclaw::get_openclaw_status(&oc_state).await;
    Ok(status.to_string())
}

/// `openclaw` 바이너리 설치 여부를 확인한다.
/// 게이트웨이 실행 여부와 무관 — 자동 설치 UI를 띄울지 결정하는 용도.
#[tauri::command]
pub async fn openclaw_installed() -> Result<String, String> {
    let info = crate::openclaw::is_openclaw_installed().await;
    Ok(info.to_string())
}

/// 게이트웨이가 18789에서 응답하도록 보장한다 (idempotent).
/// - 이미 떠 있으면 즉시 OK
/// - 아니면 자식 프로세스로 spawn 후 ready까지 대기
///
/// React 자동 설치 모달이 npm install 완료 후 호출해 즉시 온라인 전환을 트리거.
#[tauri::command]
pub async fn openclaw_ensure_running(
    oc_state: State<'_, Mutex<OpenClawState>>,
) -> Result<String, String> {
    crate::openclaw::spawn_openclaw(&oc_state).await?;
    let status = crate::openclaw::get_openclaw_status(&oc_state).await;
    Ok(status.to_string())
}

/// OpenClaw config를 Ollama 프로바이더로 맞춘다 (비인터랙티브).
/// 인자 `model` 예: "llama3.2", "qwen2.5:7b" — provider prefix는 자동으로 "ollama/" 부여.
#[tauri::command]
pub async fn openclaw_use_ollama(model: String) -> Result<String, String> {
    let result = crate::ollama::configure_openclaw_ollama(&model).await?;
    Ok(result.to_string())
}

/// 게이트웨이 메서드 호출 — health, system-presence, cron.* 등.
#[tauri::command]
pub async fn openclaw_cli_call(
    method: String,
    params: Option<serde_json::Value>,
    opts: Option<crate::openclaw_cli::CallOpts>,
) -> Result<serde_json::Value, String> {
    let opts = opts.unwrap_or_default();
    crate::openclaw_cli::gateway_call(&method, params.as_ref(), &opts)
        .await
        .map_err(|e| e.into_string())
}

/// 에이전트 한 턴 실행 — 메신저 봇 메시지를 게이트웨이로 전달할 때 사용.
#[tauri::command]
pub async fn openclaw_cli_agent(
    req: crate::openclaw_cli::AgentTurnRequest,
) -> Result<serde_json::Value, String> {
    crate::openclaw_cli::agent_turn(&req)
        .await
        .map_err(|e| e.into_string())
}

/// 설치된 OpenClaw 스킬 목록.
#[tauri::command]
pub async fn skills_installed(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/skills/installed"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("스킬 목록 조회 실패: {}", e))?;

    read_response(resp).await
}

/// ClawHub에서 스킬 설치.
#[tauri::command]
pub async fn skills_install(
    state: State<'_, Mutex<SidecarState>>,
    skill_name: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/skills/install"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "skill_name": skill_name });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(120))
        .send()
        .await
        .map_err(|e| format!("스킬 설치 요청 실패: {}", e))?;

    read_response(resp).await
}

/// ClawHub 스킬 카탈로그 조회.
#[tauri::command]
pub async fn skills_catalog(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/skills/catalog"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
        .map_err(|e| format!("스킬 카탈로그 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 사용자의 승인/거부 결정을 사이드카에 전달한다.
/// rejection_reason은 거부 시에만 의미 있으며, None이면 /agent/approval body에 포함하지 않는다.
#[tauri::command]
pub async fn agent_submit_approval(
    state: State<'_, Mutex<SidecarState>>,
    approval_id: String,
    approved: bool,
    rejection_reason: Option<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/agent/approval"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    // rejection_reason이 있으면 body에 포함 (None이면 키 자체를 제외)
    let body = match rejection_reason {
        Some(ref reason) if !approved => serde_json::json!({
            "approval_id": approval_id,
            "approved": approved,
            "rejection_reason": reason,
        }),
        _ => serde_json::json!({
            "approval_id": approval_id,
            "approved": approved,
        }),
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
        .map_err(|e| format!("승인 전달 실패: {}", e))?;

    read_response(resp).await
}

/// 대기 중인 승인 요청 목록을 조회한다 (폴링용).
#[tauri::command]
pub async fn agent_pending_approvals(
    state: State<'_, Mutex<SidecarState>>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/agent/approval/pending"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .map_err(|e| format!("승인 목록 조회 실패: {}", e))?;

    read_response(resp).await
}
