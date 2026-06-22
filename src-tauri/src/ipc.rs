use std::sync::Mutex;
use tauri::State;

use crate::openclaw::OpenClawState;
use crate::sidecar::SidecarState;

/// Helper to build the sidecar URL.
fn sidecar_url(state: &SidecarState, path: &str) -> String {
    format!("http://127.0.0.1:{}{}", state.port, path)
}

/// Helper to create a client with auth.
fn client_with_auth(state: &SidecarState) -> (reqwest::Client, String) {
    (reqwest::Client::new(), state.auth_token.clone())
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

#[tauri::command]
pub async fn health_check(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/health"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Health check failed: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn store_credential(
    state: State<'_, Mutex<SidecarState>>,
    service: String,
    value: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/credentials"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "key": service,
        "value": value
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Store credential failed: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn get_credential(
    state: State<'_, Mutex<SidecarState>>,
    service: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, &format!("/credentials/{}", service)),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Get credential failed: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn delete_credential(
    state: State<'_, Mutex<SidecarState>>,
    service: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, &format!("/credentials/{}", service)),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .delete(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Delete credential failed: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn list_credentials(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/credentials"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("List credentials failed: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn chat(
    state: State<'_, Mutex<SidecarState>>,
    message: String,
    model: Option<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/llm/chat"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "message": message,
        "model": model
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(120))
        .send()
        .await
        .map_err(|e| format!("Chat request failed: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn get_audit_logs(
    state: State<'_, Mutex<SidecarState>>,
    limit: Option<u32>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        let limit_param = limit.map_or(String::new(), |l| format!("?limit={}", l));
        (
            sidecar_url(&s, &format!("/audit/logs{}", limit_param)),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Get audit logs failed: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn gmail_status(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/gmail/status"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Gmail status failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn gmail_connect(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/gmail/connect"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("Gmail connect failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn gmail_disconnect(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/gmail/disconnect"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Gmail disconnect failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn gmail_fetch_emails(
    state: State<'_, Mutex<SidecarState>>,
    max_results: Option<u32>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        let param = max_results.map_or(String::new(), |n| format!("?max_results={}", n));
        (
            sidecar_url(&s, &format!("/gmail/emails{}", param)),
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
        .map_err(|e| format!("Gmail fetch failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn gmail_get_email_body(
    state: State<'_, Mutex<SidecarState>>,
    message_id: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, &format!("/gmail/emails/{}/body", message_id)),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Gmail get body failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn gmail_summarize_email(
    state: State<'_, Mutex<SidecarState>>,
    message_id: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, &format!("/gmail/emails/{}/summarize", message_id)),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(120))
        .send()
        .await
        .map_err(|e| format!("Gmail summarize failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn gmail_summarize_batch(
    state: State<'_, Mutex<SidecarState>>,
    max_results: Option<u32>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        let param = max_results.map_or(String::new(), |n| format!("?max_results={}", n));
        (
            sidecar_url(&s, &format!("/gmail/emails/summarize-batch{}", param)),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(300))
        .send()
        .await
        .map_err(|e| format!("Gmail batch summarize failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn get_filter_rules(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/gmail/filter-rules"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Get filter rules failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn update_filter_rules(
    state: State<'_, Mutex<SidecarState>>,
    rules: serde_json::Value,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/gmail/filter-rules"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .put(&url)
        .bearer_auth(&token)
        .json(&rules)
        .send()
        .await
        .map_err(|e| format!("Update filter rules failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn telegram_status(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/telegram/status"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Telegram status failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn telegram_start(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/telegram/start"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Telegram start failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn telegram_stop(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/telegram/stop"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Telegram stop failed: {}", e))?;
    read_response(resp).await
}

// ── Excel AI commands ──────────────────────────────────────────────────────

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

// ── Excel Live(COM) commands ────────────────────────────────────────────────

#[tauri::command]
pub async fn excel_live_status(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel-live/status"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Excel Live 상태 조회 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn excel_live_command(
    state: State<'_, Mutex<SidecarState>>,
    message: String,
    workbook_id: Option<String>,
    sheet_name: Option<String>,
    approve: Option<bool>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel-live/command"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "message": message,
        "workbook_id": workbook_id,
        "sheet_name": sheet_name,
        "approve": approve.unwrap_or(false),
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("Excel Live 명령 실행 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn excel_live_submit_approval(
    state: State<'_, Mutex<SidecarState>>,
    approval_id: String,
    approved: bool,
    rejection_reason: Option<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel-live/approval"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "approval_id": approval_id,
        "approved": approved,
        "rejection_reason": rejection_reason,
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(120))
        .send()
        .await
        .map_err(|e| format!("Excel Live 승인 응답 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn excel_live_save_workbook(
    state: State<'_, Mutex<SidecarState>>,
    workbook_id: Option<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/excel-live/action"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "action": "excel_live.save_workbook",
        "params": {},
        "workbook_id": workbook_id,
        "sheet_name": serde_json::Value::Null,
        "approve": true,
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await
        .map_err(|e| format!("Excel 저장 요청 실패: {}", e))?;

    read_response(resp).await
}

// ── Document AI commands ────────────────────────────────────────────────────

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

// ── Gmail AI commands ────────────────────────────────────────────────────────

#[tauri::command]
pub async fn gmail_draft_reply(
    state: State<'_, Mutex<SidecarState>>,
    email_id: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, &format!("/gmail/emails/{}/draft-reply", email_id)),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("AI 답장 초안 요청 실패: {}", e))?;

    read_response(resp).await
}

#[tauri::command]
pub async fn gmail_prioritize(
    state: State<'_, Mutex<SidecarState>>,
    email_ids: Vec<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/gmail/emails/prioritize"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "email_ids": email_ids });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|e| format!("우선순위 분석 요청 실패: {}", e))?;

    read_response(resp).await
}

// ── Maintenance commands ─────────────────────────────────────────────────────

#[tauri::command]
pub async fn maintenance_cleanup(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/maintenance/cleanup"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("임시 파일 정리 요청 실패: {}", e))?;

    read_response(resp).await
}

// ── Phase 4: Agent / OpenClaw commands ─────────────────────────────────────

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

// ── OpenClaw CLI 서브프로세스 wrapper (2026-05-20) ───────────────────────────
//
// 게이트웨이에 WebSocket으로 직접 붙는 대신, `openclaw gateway call` / `openclaw agent`
// 서브프로세스를 spawn해 결과 JSON을 받는다. 게이트웨이 프로토콜 변경 내성이 강하다.

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

// ── Phase 5: Agent Approval commands ────────────────────────────────────────

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

// ── Phase 5: Security Dashboard commands ─────────────────────────────────────

/// 마스킹/차단 통계를 반환한다.
#[tauri::command]
pub async fn security_stats(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/security/stats"),
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
        .map_err(|e| format!("보안 통계 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 최근 보안 차단 이벤트 목록을 반환한다.
#[tauri::command]
pub async fn security_blocked_log(
    state: State<'_, Mutex<SidecarState>>,
    limit: Option<u32>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        let limit_param = limit.map_or(String::new(), |l| format!("?limit={}", l));
        (
            sidecar_url(&s, &format!("/security/blocked-log{}", limit_param)),
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
        .map_err(|e| format!("차단 이력 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 현재 스킬별 권한 설정을 반환한다.
#[tauri::command]
pub async fn security_get_whitelist(
    state: State<'_, Mutex<SidecarState>>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/security/whitelist"),
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
        .map_err(|e| format!("화이트리스트 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 스킬별 권한을 업데이트한다.
#[tauri::command]
pub async fn security_update_whitelist(
    state: State<'_, Mutex<SidecarState>>,
    overrides: serde_json::Value,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/security/whitelist"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "overrides": overrides });

    let resp = client
        .put(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("화이트리스트 저장 실패: {}", e))?;

    read_response(resp).await
}

/// 현재 마스킹 설정(mask_email, mask_phone)을 반환한다.
#[tauri::command]
pub async fn security_get_masking_settings(
    state: State<'_, Mutex<SidecarState>>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/security/masking-settings"),
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
        .map_err(|e| format!("마스킹 설정 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 마스킹 설정을 업데이트한다 (mask_email, mask_phone).
#[tauri::command]
pub async fn security_update_masking_settings(
    state: State<'_, Mutex<SidecarState>>,
    mask_email: bool,
    mask_phone: bool,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/security/masking-settings"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "mask_email": mask_email, "mask_phone": mask_phone });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("마스킹 설정 저장 실패: {}", e))?;

    read_response(resp).await
}

// ── Phase 3: Slack commands ──────────────────────────────────────────────────

#[tauri::command]
pub async fn slack_setup(
    state: State<'_, Mutex<SidecarState>>,
    bot_token: String,
    app_token: String,
    allowed_user_ids: Option<Vec<String>>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/slack/setup"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };
    let body = serde_json::json!({
        "bot_token": bot_token,
        "app_token": app_token,
        "allowed_user_ids": allowed_user_ids,
    });
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| format!("Slack 설정 실패: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn slack_status(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/slack/status"),
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
        .map_err(|e| format!("Slack 상태 조회 실패: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn slack_start(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/slack/start"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Slack 시작 실패: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn slack_stop(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/slack/stop"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Slack 중지 실패: {}", e))?;
    read_response(resp).await
}

// ── Phase 3: Discord commands ────────────────────────────────────────────────

#[tauri::command]
pub async fn discord_setup(
    state: State<'_, Mutex<SidecarState>>,
    token: String,
    allowed_guild_id: Option<String>,
    allowed_user_ids: Option<Vec<String>>,
) -> Result<String, String> {
    let (url, client, auth_token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/discord/setup"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };
    let body = serde_json::json!({
        "token": token,
        "allowed_guild_id": allowed_guild_id,
        "allowed_user_ids": allowed_user_ids,
    });
    let resp = client
        .post(&url)
        .bearer_auth(&auth_token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| format!("Discord 설정 실패: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn discord_status(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/discord/status"),
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
        .map_err(|e| format!("Discord 상태 조회 실패: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn discord_start(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/discord/start"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Discord 시작 실패: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn discord_stop(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/discord/stop"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Discord 중지 실패: {}", e))?;
    read_response(resp).await
}

// ── Phase 3: Permissions commands ────────────────────────────────────────────

#[tauri::command]
pub async fn permissions_get(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/permissions"),
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
        .map_err(|e| format!("권한 설정 조회 실패: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn permissions_update(
    state: State<'_, Mutex<SidecarState>>,
    allowed_folders: Vec<String>,
    allowed_apps: Vec<String>,
    shell_command_whitelist: Vec<String>,
    python_module_whitelist: Vec<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/permissions"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };
    let body = serde_json::json!({
        "allowed_folders": allowed_folders,
        "allowed_apps": allowed_apps,
        "shell_command_whitelist": shell_command_whitelist,
        "python_module_whitelist": python_module_whitelist,
    });
    let resp = client
        .put(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("권한 설정 저장 실패: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn permissions_whitelist_add(
    state: State<'_, Mutex<SidecarState>>,
    command: String,
    command_type: String,
    reason: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/permissions/whitelist"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };
    let body =
        serde_json::json!({ "command": command, "command_type": command_type, "reason": reason });
    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("화이트리스트 추가 실패: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn permissions_whitelist_remove(
    state: State<'_, Mutex<SidecarState>>,
    command: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        let encoded = urlencoding_simple(&command);
        (
            sidecar_url(&s, &format!("/permissions/whitelist/{}", encoded)),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };
    let resp = client
        .delete(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("화이트리스트 제거 실패: {}", e))?;
    read_response(resp).await
}

// ── Phase 2: Security UI Approval commands ───────────────────────────────────

/// 대기 중인 보안 승인 요청 목록을 반환한다 (UI 폴링용).
#[tauri::command]
pub async fn security_get_pending_approvals(
    state: State<'_, Mutex<SidecarState>>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/security/approval/pending"),
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
        .map_err(|e| format!("보안 승인 목록 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 보안 승인 요청에 승인 또는 거부로 응답한다.
#[tauri::command]
pub async fn security_respond_approval(
    state: State<'_, Mutex<SidecarState>>,
    approval_id: String,
    approved: bool,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, &format!("/security/approval/{}/respond", approval_id)),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "approved": approved });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("보안 승인 응답 전달 실패: {}", e))?;

    read_response(resp).await
}

// ── Phase 2: Command Audit Log commands ──────────────────────────────────────

/// 명령 감사 로그 목록을 반환한다.
#[tauri::command]
pub async fn command_audit_list(
    state: State<'_, Mutex<SidecarState>>,
    limit: Option<u32>,
    offset: Option<u32>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        let limit_val = limit.unwrap_or(50);
        let offset_val = offset.unwrap_or(0);
        (
            sidecar_url(
                &s,
                &format!("/security/audit?limit={}&offset={}", limit_val, offset_val),
            ),
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
        .map_err(|e| format!("명령 감사 로그 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 명령 감사 로그 등급별 통계를 반환한다.
#[tauri::command]
pub async fn command_audit_stats(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/security/audit/stats"),
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
        .map_err(|e| format!("명령 감사 통계 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 명령 감사 로그 전체를 초기화한다.
#[tauri::command]
pub async fn command_audit_clear(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/security/audit"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .delete(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("명령 감사 로그 초기화 실패: {}", e))?;

    read_response(resp).await
}

// ── Phase 1: Private-Claw — Workspace commands ──────────────────────────────

/// 워크스페이스 폴더를 Finder(macOS) / Explorer(Windows)로 연다.
#[tauri::command]
pub async fn open_workspace_folder(_app: tauri::AppHandle) -> Result<String, String> {
    let home = dirs_home().ok_or_else(|| "홈 디렉토리를 찾을 수 없습니다".to_string())?;
    let workspace = home.join("PrivateClaw").join("Workspace");

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
    let home = dirs_home().ok_or_else(|| "홈 디렉토리를 찾을 수 없습니다".to_string())?;
    let workspace_root = home.join("PrivateClaw").join("Workspace");
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
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        let path_param = path
            .as_deref()
            .filter(|p| !p.is_empty())
            .map(|p| format!("?path={}", urlencoding_simple(p)))
            .unwrap_or_default();
        (
            sidecar_url(&s, &format!("/workspace/files{}", path_param)),
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
        .map_err(|e| format!("워크스페이스 파일 목록 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 워크스페이스 내 파일 내용을 읽는다.
#[tauri::command]
pub async fn workspace_read_file(
    state: State<'_, Mutex<SidecarState>>,
    path: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(
                &s,
                &format!("/workspace/file?path={}", urlencoding_simple(&path)),
            ),
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
        .map_err(|e| format!("파일 읽기 실패: {}", e))?;

    read_response(resp).await
}

/// 워크스페이스 내 파일에 내용을 쓴다.
#[tauri::command]
pub async fn workspace_write_file(
    state: State<'_, Mutex<SidecarState>>,
    path: String,
    content: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/workspace/file"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "path": path, "content": content });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("파일 쓰기 실패: {}", e))?;

    read_response(resp).await
}

/// 워크스페이스에 새 엑셀(.xlsx) 파일을 생성한다.
#[tauri::command]
pub async fn workspace_create_excel_file(
    state: State<'_, Mutex<SidecarState>>,
    path: String,
    sheet_name: Option<String>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/workspace/excel-file"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "path": path,
        "sheet_name": sheet_name,
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("엑셀 파일 생성 실패: {}", e))?;

    read_response(resp).await
}

/// 워크스페이스에 base64 인코딩된 바이너리 파일을 쓴다.
///
/// 보안 샌드박스:
///   - 경로 내 `..` 컴포넌트 차단 (디렉토리 탈출 방지)
///   - 절대 경로 차단 — 반드시 상대 경로 사용
///   - 최종 경로는 ~/PrivateClaw/Workspace/{path} 고정
///
/// 호출 측(Frontend)은 base64 표준 인코딩(RFC 4648) 문자열을 전달해야 한다.
#[tauri::command]
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
    let home = dirs_home().ok_or_else(|| "홈 디렉토리를 찾을 수 없습니다".to_string())?;
    let workspace_root = home.join("PrivateClaw").join("Workspace");
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

/// 텔레그램 봇 토큰을 설정하고 연결 테스트를 수행한다.
#[tauri::command]
pub async fn telegram_setup(
    state: State<'_, Mutex<SidecarState>>,
    token: String,
    chat_id: Option<String>,
) -> Result<String, String> {
    let (url, client, auth_token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/telegram/setup"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "token": token, "chat_id": chat_id });

    let resp = client
        .post(&url)
        .bearer_auth(&auth_token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| format!("텔레그램 설정 요청 실패: {}", e))?;

    read_response(resp).await
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
#[tauri::command]
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
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/chat/messages"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({
        "session_id": session_id,
        "role": role,
        "text": text,
        "tool_calls": tool_calls,
        "masked_count": masked_count.unwrap_or(0),
        "masked_types": masked_types,
        "error_text": error_text,
    });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("채팅 메시지 저장 실패: {}", e))?;

    read_response(resp).await
}

/// 채팅 세션 목록을 최근 활동순으로 반환한다.
#[tauri::command]
pub async fn chat_list_sessions(
    state: State<'_, Mutex<SidecarState>>,
    limit: Option<u32>,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        let limit_param = limit.map_or(String::new(), |l| format!("?limit={}", l));
        (
            sidecar_url(&s, &format!("/chat/sessions{}", limit_param)),
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
        .map_err(|e| format!("채팅 세션 목록 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 세션의 전체 메시지를 반환한다.
#[tauri::command]
pub async fn chat_get_messages(
    state: State<'_, Mutex<SidecarState>>,
    session_id: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(
                &s,
                &format!(
                    "/chat/sessions/{}/messages",
                    urlencoding_simple(&session_id)
                ),
            ),
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
        .map_err(|e| format!("채팅 메시지 조회 실패: {}", e))?;

    read_response(resp).await
}

/// 세션과 하위 메시지를 모두 삭제한다.
#[tauri::command]
pub async fn chat_delete_session(
    state: State<'_, Mutex<SidecarState>>,
    session_id: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(
                &s,
                &format!("/chat/sessions/{}", urlencoding_simple(&session_id)),
            ),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .delete(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await
        .map_err(|e| format!("채팅 세션 삭제 실패: {}", e))?;

    read_response(resp).await
}

// ── Sprint 5: 백업/내보내기 ───────────────────────────────────────────────────

/// 현재 데이터를 ~/Downloads/ajou-ai-backup-{timestamp}.zip으로 내보낸다.
#[tauri::command]
pub async fn backup_export(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/backup/export"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .timeout(std::time::Duration::from_secs(60))
        .send()
        .await
        .map_err(|e| format!("백업 export 실패: {}", e))?;

    read_response(resp).await
}

/// 지정된 zip 파일로부터 데이터를 복원한다.
#[tauri::command]
pub async fn backup_import(
    state: State<'_, Mutex<SidecarState>>,
    file_path: String,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/backup/import"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let body = serde_json::json!({ "file_path": file_path });

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&body)
        .timeout(std::time::Duration::from_secs(60))
        .send()
        .await
        .map_err(|e| format!("백업 import 실패: {}", e))?;

    read_response(resp).await
}

// ── Private helpers ─────────────────────────────────────────────────────────

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

fn dirs_home() -> Option<std::path::PathBuf> {
    std::env::var_os("HOME")
        .map(std::path::PathBuf::from)
        .or_else(|| std::env::var_os("USERPROFILE").map(std::path::PathBuf::from))
}

#[tauri::command]
pub async fn get_llm_settings(state: State<'_, Mutex<SidecarState>>) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/settings/llm"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .get(&url)
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("Get LLM settings failed: {}", e))?;
    read_response(resp).await
}

#[tauri::command]
pub async fn save_llm_settings(
    state: State<'_, Mutex<SidecarState>>,
    config: serde_json::Value,
) -> Result<String, String> {
    let (url, client, token) = {
        let s = state.lock().map_err(|e| e.to_string())?;
        (
            sidecar_url(&s, "/settings/llm"),
            client_with_auth(&s).0,
            s.auth_token.clone(),
        )
    };

    let resp = client
        .post(&url)
        .bearer_auth(&token)
        .json(&config)
        .send()
        .await
        .map_err(|e| format!("Save LLM settings failed: {}", e))?;
    read_response(resp).await
}
