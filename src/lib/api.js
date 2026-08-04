/**
 * Tauri IPC + Python sidecar API wrapper.
 *
 * All communication with the Python FastAPI sidecar goes through Tauri's
 * `invoke()` command which proxies the HTTP request with Bearer auth.
 *
 * Pattern mirrors ipc.rs: each function maps 1-to-1 to a Tauri command.
 */

// Tauri v2 — @tauri-apps/api/core exposes `invoke`
import { invoke } from "@tauri-apps/api/core";

// ── Helpers ───────────────────────────────────────────────────────────────

/**
 * Generic invoke wrapper that surfaces errors as thrown strings (matching
 * the Rust `Result<String, String>` convention in ipc.rs).
 *
 * @template T
 * @param {string} cmd - Tauri command name
 * @param {Record<string, unknown>} [args]
 * @returns {Promise<T>}
 */
async function call(cmd, args = {}) {
  return invoke(cmd, args);
}

/**
 * Parse a JSON string returned from the sidecar.
 *
 * - If parsing succeeds, return the parsed value.
 * - If parsing fails AND the raw string looks like an error message
 *   (non-JSON text from Rust/Python), throw it as an Error so callers
 *   can catch it and display a user-friendly message.
 * - If the raw string is genuinely not JSON but a plain success string
 *   (e.g. "ok"), return it as-is.
 *
 * @template T
 * @param {string} raw
 * @returns {T}
 * @throws {Error} when the sidecar returns a detectable error string
 */
export function parseResponse(raw) {
  // Attempt JSON parse first
  try {
    const parsed = JSON.parse(raw);

    // If the sidecar returned a JSON object with an `error` or `detail` field,
    // surface it as a thrown error so catch blocks receive it correctly.
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      if (typeof parsed.error === "string" && parsed.error) {
        throw new Error(parsed.error);
      }
      if (typeof parsed.detail === "string" && parsed.detail) {
        throw new Error(parsed.detail);
      }
    }

    return parsed;
  } catch (jsonError) {
    // If we threw explicitly above, re-throw
    if (jsonError instanceof SyntaxError === false) {
      throw jsonError;
    }
    // raw is not JSON — return as plain string (e.g. "ok", "deleted")
    return /** @type {T} */ (raw);
  }
}

// ── Health ────────────────────────────────────────────────────────────────

/** Check that the Python sidecar is running. */
export async function healthCheck() {
  const raw = await call("health_check");
  return parseResponse(raw);
}

// ── Credentials ───────────────────────────────────────────────────────────

export async function storeCredential(service, value) {
  const raw = await call("store_credential", { service, value });
  return parseResponse(raw);
}

export async function getCredential(service) {
  const raw = await call("get_credential", { service });
  return parseResponse(raw);
}

export async function deleteCredential(service) {
  const raw = await call("delete_credential", { service });
  return parseResponse(raw);
}

export async function listCredentials() {
  const raw = await call("list_credentials");
  return parseResponse(raw);
}

// ── LLM ──────────────────────────────────────────────────────────────────

/**
 * Send a chat message to the currently configured LLM engine.
 *
 * @param {string} message
 * @param {string} [model]
 * @returns {Promise<{ response: string }>}
 */
export async function chat(message, model) {
  const raw = await call("chat", { message, model: model ?? null });
  return parseResponse(raw);
}

// ── Settings (LLM config) ─────────────────────────────────────────────────

export async function getLLMSettings() {
  const raw = await call("get_llm_settings");
  return parseResponse(raw);
}

/**
 * @param {{ provider: 'ollama'|'claude', model: string }} config
 */
export async function saveLLMSettings(config) {
  const raw = await call("save_llm_settings", { config });
  return parseResponse(raw);
}

// ── Audit ─────────────────────────────────────────────────────────────────

export async function getAuditLogs(limit) {
  const raw = await call("get_audit_logs", { limit: limit ?? null });
  return parseResponse(raw);
}

// ── Gmail ─────────────────────────────────────────────────────────────────

export async function gmailStatus() {
  const raw = await call("gmail_status");
  return parseResponse(raw);
}

export async function gmailConnect() {
  const raw = await call("gmail_connect");
  return parseResponse(raw);
}

export async function gmailDisconnect() {
  const raw = await call("gmail_disconnect");
  return parseResponse(raw);
}

export async function gmailFetchEmails(maxResults) {
  const raw = await call("gmail_fetch_emails", {
    max_results: maxResults ?? null,
  });
  return parseResponse(raw);
}

export async function gmailGetEmailBody(messageId) {
  const raw = await call("gmail_get_email_body", { message_id: messageId });
  return parseResponse(raw);
}

export async function gmailSummarizeEmail(messageId) {
  const raw = await call("gmail_summarize_email", { message_id: messageId });
  return parseResponse(raw);
}

export async function gmailSummarizeBatch(maxResults) {
  const raw = await call("gmail_summarize_batch", {
    max_results: maxResults ?? null,
  });
  return parseResponse(raw);
}

/**
 * Generate a professional Korean reply draft for an email.
 *
 * @param {string} emailId
 * @returns {Promise<{ draft: string }>}
 */
export async function gmailDraftReply(emailId) {
  const raw = await call("gmail_draft_reply", { email_id: emailId });
  return parseResponse(raw);
}

/**
 * Classify emails by urgency using AI.
 *
 * @param {string[]} emailIds
 * @returns {Promise<{ priorities: Array<{ id: string, tag: string, reason: string }> }>}
 */
export async function gmailPrioritize(emailIds) {
  const raw = await call("gmail_prioritize", { email_ids: emailIds });
  return parseResponse(raw);
}

export async function getFilterRules() {
  const raw = await call("get_filter_rules");
  return parseResponse(raw);
}

export async function updateFilterRules(rules) {
  const raw = await call("update_filter_rules", { rules });
  return parseResponse(raw);
}

// ── Telegram ──────────────────────────────────────────────────────────────

export async function telegramStatus() {
  const raw = await call("telegram_status");
  return parseResponse(raw);
}

export async function telegramStart() {
  const raw = await call("telegram_start");
  return parseResponse(raw);
}

export async function telegramStop() {
  const raw = await call("telegram_stop");
  return parseResponse(raw);
}

// ── Excel AI ──────────────────────────────────────────────────────────────

/**
 * Upload an xlsx/csv file by local path.
 * Returns { file_id, filename, sheet_names, row_count, col_count, columns, ... }
 *
 * @param {string} filePath - Absolute local file path
 */
export async function excelUpload(filePath) {
  const raw = await call("excel_upload", { file_path: filePath });
  return parseResponse(raw);
}

/**
 * Ask a natural-language question about an uploaded spreadsheet.
 *
 * @param {string} fileId
 * @param {string} question
 * @returns {Promise<{ answer: string }>}
 */
export async function excelAnalyze(fileId, question) {
  const raw = await call("excel_analyze", { file_id: fileId, question });
  return parseResponse(raw);
}

/**
 * Auto-generate a comprehensive Korean data analysis report.
 *
 * @param {string} fileId
 * @returns {Promise<{ report: string }>}
 */
export async function excelReport(fileId) {
  const raw = await call("excel_report", { file_id: fileId });
  return parseResponse(raw);
}

/**
 * Get Excel formula suggestions based on the file's column structure.
 *
 * @param {string} fileId
 * @returns {Promise<{ suggestions: string }>}
 */
export async function excelFormulas(fileId) {
  const raw = await call("excel_formulas", { file_id: fileId });
  return parseResponse(raw);
}

/**
 * Extract numeric columns as Chart.js-ready data.
 *
 * @param {string} fileId
 * @param {string} sheetName
 * @returns {Promise<{ labels: Array, datasets: Array }>}
 */
export async function excelChartData(fileId, sheetName) {
  const raw = await call("excel_chart_data", {
    file_id: fileId,
    sheet_name: sheetName,
  });
  return parseResponse(raw);
}

/**
 * Export the analysis report appended as a new sheet to the original workbook.
 * Saves to Downloads folder and returns the saved file path.
 *
 * @param {string} fileId
 * @param {string} reportMarkdown
 * @returns {Promise<string>} Saved file path
 */
export async function excelExport(fileId, reportMarkdown) {
  const raw = await call("excel_export", {
    file_id: fileId,
    report_markdown: reportMarkdown,
  });
  return parseResponse(raw);
}

// ── Excel Live(COM) ────────────────────────────────────────────────────────

/**
 * Excel Live 연결 상태와 열린 워크북 목록을 조회한다.
 */
export async function excelLiveStatus() {
  const raw = await call("excel_live_status");
  return parseResponse(raw);
}

/**
 * 자연어 엑셀 명령을 실행한다.
 *
 * @param {string} message
 * @param {string | null} workbookId
 * @param {string | null} sheetName
 * @param {string | null} sessionId
 * @param {boolean} approve
 * @param {string | null} contextRange
 */
export async function excelLiveCommand(
  message,
  workbookId = null,
  sheetName = null,
  sessionId = null,
  approve = false,
  contextRange = null,
) {
  const raw = await call("excel_live_command", {
    message,
    workbookId,
    sheetName,
    sessionId,
    approve,
    contextRange,
  });
  return parseResponse(raw);
}

/**
 * Excel Live 승인 요청에 대해 승인/거부를 전달한다.
 *
 * @param {string} approvalId
 * @param {boolean} approved
 * @param {string | null} reason
 */
export async function excelLiveSubmitApproval(approvalId, approved, reason = null) {
  const raw = await call("excel_live_submit_approval", {
    approvalId,
    approved,
    rejectionReason: reason,
  });
  return parseResponse(raw);
}

/**
 * 현재 Excel Live 대상 통합문서를 즉시 저장한다.
 *
 * @param {string | null} workbookId
 */
export async function excelLiveSaveWorkbook(workbookId = null) {
  const raw = await call("excel_live_save_workbook", { workbookId });
  return parseResponse(raw);
}

/**
 * 현재 통합문서 기준 복구 백업 목록(최신순)을 조회한다.
 *
 * @param {string | null} workbookId
 * @param {number} limit
 */
export async function excelLiveListBackups(workbookId = null, limit = 20) {
  const raw = await call("excel_live_list_backups", { workbookId, limit });
  return parseResponse(raw);
}

/**
 * 가장 최근(또는 지정) 백업 파일로 통합문서를 복구한다.
 *
 * @param {string | null} workbookId
 * @param {string | null} backupPath
 */
export async function excelLiveRestoreLastBackup(workbookId = null, backupPath = null) {
  const raw = await call("excel_live_restore_last_backup", { workbookId, backupPath });
  return parseResponse(raw);
}

// ── Harness feedback/replay ───────────────────────────────────────────────

/**
 * 사용자 피드백(좋아요/아쉬워요)을 하네스에 저장한다.
 */
export async function harnessFeedback({
  userId = null,
  sessionId = null,
  route = "/excel-live/command",
  message = "",
  rating,
  reason = "",
  expectedAction = "",
  expectedBehavior = "",
}) {
  const raw = await call("harness_feedback", {
    userId,
    sessionId,
    route,
    message,
    rating,
    reason,
    expectedAction,
    expectedBehavior,
  });
  return parseResponse(raw);
}

/**
 * 실패 케이스 리플레이(학습 루프)를 수동 실행한다.
 */
export async function harnessReplayFailures({
  userId = null,
  sessionId = null,
  route = "/excel-live/command",
  limit = 20,
  parseTimeoutSeconds = 10,
  minGateCases = 5,
  minGatePassRate = 0.7,
} = {}) {
  const raw = await call("harness_replay_failures", {
    userId,
    sessionId,
    route,
    limit,
    parseTimeoutSeconds,
    minGateCases,
    minGatePassRate,
  });
  return parseResponse(raw);
}

/**
 * 현재 개인화 스냅샷(활성 프롬프트/통계)을 조회한다.
 */
export async function harnessPersonalization(userId = null, sessionId = null) {
  const raw = await call("harness_personalization", { userId, sessionId });
  return parseResponse(raw);
}

// ── Document AI ───────────────────────────────────────────────────────────

/**
 * Generate a Korean document draft.
 *
 * @param {string} docType  - 보고서 | 기획안 | 회의록 | 계약서초안 | 이메일 | 제안서
 * @param {string} content  - Core content / key points
 * @param {string} tone     - 공식적 | 친근한 | 전문적
 * @param {string} length   - 짧게 | 보통 | 길게
 * @returns {Promise<{ draft: string }>}
 */
export async function documentGenerate(docType, content, tone, length) {
  const raw = await call("document_generate", {
    doc_type: docType,
    content,
    tone,
    length,
  });
  return parseResponse(raw);
}

/**
 * Export document as Word (.docx). Saves to Downloads, returns saved path.
 *
 * @param {string} title
 * @param {string} content - Markdown content
 * @returns {Promise<string>} Saved file path
 */
export async function documentExportDocx(title, content) {
  const raw = await call("document_export_docx", { title, content });
  return parseResponse(raw);
}

/**
 * Export document as PDF. Saves to Downloads, returns saved path.
 *
 * @param {string} title
 * @param {string} content - Markdown content
 * @returns {Promise<string>} Saved file path
 */
export async function documentExportPdf(title, content) {
  const raw = await call("document_export_pdf", { title, content });
  return parseResponse(raw);
}

// ── Phase 5: Agent Approval ───────────────────────────────────────────────────

/**
 * 승인/거부 결정을 사이드카에 전달한다.
 *
 * @param {string} approvalId
 * @param {boolean} approved
 * @param {string} [reason] - 거부 사유 (선택, 30자 이내). sidecar가 받지 않으면 무시됨.
 * @returns {Promise<{ ok: boolean, approved: boolean, tool_name: string }>}
 */
export async function agentSubmitApproval(approvalId, approved, reason) {
  // rejection_reason 키로 Tauri command에 전달 (N-1 Sprint 4 — ipc.rs 시그니처 정렬)
  const args = { approval_id: approvalId, approved };
  if (!approved && reason && typeof reason === "string") {
    args.rejection_reason = reason;
  }
  const raw = await call("agent_submit_approval", args);
  return parseResponse(raw);
}

/**
 * 대기 중인 승인 요청 목록을 조회한다 (폴링용).
 *
 * @returns {Promise<{ pending: Array }>}
 */
export async function agentPendingApprovals() {
  const raw = await call("agent_pending_approvals");
  return parseResponse(raw);
}

// ── Phase 5: Security Dashboard ───────────────────────────────────────────────

/**
 * 마스킹/차단 통계를 반환한다.
 *
 * @returns {Promise<{ masking: object, blocked_count: object }>}
 */
export async function securityStats() {
  const raw = await call("security_stats");
  return parseResponse(raw);
}

/**
 * 최근 보안 차단 이벤트 목록을 반환한다.
 *
 * @param {number} [limit]
 * @returns {Promise<{ logs: Array, count: number }>}
 */
export async function securityBlockedLog(limit) {
  const raw = await call("security_blocked_log", { limit: limit ?? null });
  return parseResponse(raw);
}

/**
 * 현재 스킬별 권한 설정을 반환한다.
 *
 * @returns {Promise<{ skills: Array }>}
 */
export async function securityGetWhitelist() {
  const raw = await call("security_get_whitelist");
  return parseResponse(raw);
}

/**
 * 스킬별 권한을 업데이트한다.
 *
 * @param {Record<string, string>} overrides  {"gog.gmail.send": "safe", ...}
 * @returns {Promise<{ ok: boolean, updated: number }>}
 */
export async function securityUpdateWhitelist(overrides) {
  const raw = await call("security_update_whitelist", { overrides });
  return parseResponse(raw);
}

/**
 * 현재 마스킹 설정(mask_email, mask_phone)을 반환한다.
 *
 * @returns {Promise<{ mask_email: boolean, mask_phone: boolean }>}
 */
export async function securityGetMaskingSettings() {
  const raw = await call("security_get_masking_settings");
  return parseResponse(raw);
}

/**
 * 마스킹 설정을 업데이트한다.
 *
 * @param {{ mask_email: boolean, mask_phone: boolean }} settings
 * @returns {Promise<{ ok: boolean, mask_email: boolean, mask_phone: boolean }>}
 */
export async function securityUpdateMaskingSettings(settings) {
  const raw = await call("security_update_masking_settings", settings);
  return parseResponse(raw);
}

// ── Phase 2: Command Audit Log ────────────────────────────────────────────────

/**
 * 명령 감사 로그 목록을 반환한다 (최신순).
 *
 * @param {number} [limit]  - 최대 반환 건수 (기본 50)
 * @param {number} [offset] - 건너뛸 건수 (페이지네이션)
 * @returns {Promise<{ logs: Array, count: number, offset: number }>}
 */
export async function getCommandAuditLogs(limit, offset) {
  const raw = await call("command_audit_list", {
    limit: limit ?? null,
    offset: offset ?? null,
  });
  return parseResponse(raw);
}

/**
 * 명령 분석 등급별 통계를 반환한다.
 *
 * @returns {Promise<{ total: number, safe: number, confirm: number, denied: number, confirm_approved: number, confirm_rejected: number }>}
 */
export async function getCommandAuditStats() {
  const raw = await call("command_audit_stats");
  return parseResponse(raw);
}

/**
 * 명령 감사 로그 전체를 초기화한다.
 *
 * @returns {Promise<{ ok: boolean, deleted: number }>}
 */
export async function clearCommandAuditLogs() {
  const raw = await call("command_audit_clear");
  return parseResponse(raw);
}

// ── Phase 2: Security UI Approval (텔레그램 미연결 대체 수단) ────────────────────

/**
 * 대기 중인 보안 승인 요청 목록을 반환한다 (폴링용).
 *
 * @returns {Promise<{ pending: Array<{ approval_id: string, command: string, reason: string, audit_id: number|null }> }>}
 */
export async function securityGetPendingApprovals() {
  const raw = await call("security_get_pending_approvals");
  return parseResponse(raw);
}

/**
 * 보안 승인 요청에 응답한다 (승인 또는 거부).
 *
 * @param {string} approvalId
 * @param {boolean} approved
 * @returns {Promise<{ ok: boolean, approved: boolean, approval_id: string }>}
 */
export async function securityRespondApproval(approvalId, approved) {
  const raw = await call("security_respond_approval", { approval_id: approvalId, approved });
  return parseResponse(raw);
}

// ── Phase 1: Private-Claw — Workspace ────────────────────────────────────────

/**
 * 워크스페이스 파일 목록을 반환한다.
 *
 * @param {string} [path] - 워크스페이스 기준 상대 경로 (기본값: 루트)
 * @returns {Promise<{ files: Array<{ name: string, path: string, size: number, modified: number, is_dir: boolean }>, workspace: string }>}
 */
export async function workspaceListFiles(path) {
  const raw = await call("workspace_list_files", { path: path ?? null });
  return parseResponse(raw);
}

/**
 * 워크스페이스 내 파일 내용을 읽는다.
 *
 * @param {string} path - 파일 경로
 * @returns {Promise<{ path: string, content: string, size: number }>}
 */
export async function workspaceReadFile(path) {
  const raw = await call("workspace_read_file", { path });
  return parseResponse(raw);
}

/**
 * 워크스페이스 내 파일에 내용을 쓴다.
 *
 * @param {string} path - 파일 경로
 * @param {string} content - 저장할 내용
 * @returns {Promise<{ ok: boolean, path: string }>}
 */
export async function workspaceWriteFile(path, content) {
  const raw = await call("workspace_write_file", { path, content });
  return parseResponse(raw);
}

/**
 * 워크스페이스에 새 엑셀(.xlsx) 파일을 생성한다.
 *
 * @param {string} path - 파일 경로 (확장자 .xlsx는 생략 가능)
 * @param {string} [sheetName] - 첫 시트명 (기본: Sheet1)
 * @returns {Promise<{ ok: boolean, path: string, sheet_name: string }>}
 */
export async function workspaceCreateExcelFile(path, sheetName = "Sheet1") {
  const raw = await call("workspace_create_excel_file", {
    path,
    sheet_name: sheetName,
  });
  return parseResponse(raw);
}

/**
 * 워크스페이스에 바이너리 파일을 base64로 인코딩하여 쓴다.
 * sidecar에 `workspace_write_file_binary`가 없으면 throw — 호출자가 fallback 처리.
 *
 * @param {string} path
 * @param {string} contentBase64 - base64 인코딩된 내용
 * @returns {Promise<{ ok: boolean, path: string }>}
 */
export async function workspaceWriteFileBinary(path, contentBase64) {
  const raw = await call("workspace_write_file_binary", {
    path,
    content_base64: contentBase64,
  });
  return parseResponse(raw);
}

/**
 * 워크스페이스 폴더를 Finder/Explorer로 연다.
 *
 * @returns {Promise<string>} 워크스페이스 경로
 */
export async function openWorkspaceFolder() {
  const raw = await call("open_workspace_folder");
  return parseResponse(raw);
}

/**
 * 워크스페이스 내 파일을 OS 기본 앱으로 연다.
 *
 * @param {string} path - 워크스페이스 기준 상대 경로
 * @returns {Promise<{ ok: boolean, path: string, absolute_path: string }>}
 */
export async function openWorkspaceFile(path) {
  const raw = await call("open_workspace_file", { path });
  return parseResponse(raw);
}

// ── Phase 3: Slack ────────────────────────────────────────────────────────────

/**
 * Slack 봇 토큰을 설정하고 연결 테스트를 수행한다.
 *
 * @param {string} botToken
 * @param {string} appToken
 * @param {string[]} [allowedUserIds]
 * @returns {Promise<{ ok: boolean, bot_name?: string, team?: string, error?: string }>}
 */
export async function slackSetup(botToken, appToken, allowedUserIds) {
  const raw = await call("slack_setup", {
    bot_token: botToken,
    app_token: appToken,
    allowed_user_ids: allowedUserIds ?? null,
  });
  return parseResponse(raw);
}

/**
 * Slack 봇 상태를 확인한다.
 *
 * @returns {Promise<{ configured: boolean, running: boolean }>}
 */
export async function slackStatus() {
  const raw = await call("slack_status");
  return parseResponse(raw);
}

/**
 * Slack 봇을 시작한다.
 *
 * @returns {Promise<{ status: string }>}
 */
export async function slackStart() {
  const raw = await call("slack_start");
  return parseResponse(raw);
}

/**
 * Slack 봇을 정지한다 (sidecar에 명령이 없으면 graceful fail).
 *
 * @returns {Promise<{ status: string }>}
 */
export async function slackStop() {
  const raw = await call("slack_stop");
  return parseResponse(raw);
}

// ── Phase 3: Discord ──────────────────────────────────────────────────────────

/**
 * Discord 봇 토큰을 설정하고 연결 테스트를 수행한다.
 *
 * @param {string} token
 * @param {string} [allowedGuildId]
 * @param {string[]} [allowedUserIds]
 * @returns {Promise<{ ok: boolean, bot_username?: string, error?: string }>}
 */
export async function discordSetup(token, allowedGuildId, allowedUserIds) {
  const raw = await call("discord_setup", {
    token,
    allowed_guild_id: allowedGuildId ?? null,
    allowed_user_ids: allowedUserIds ?? null,
  });
  return parseResponse(raw);
}

/**
 * Discord 봇 상태를 확인한다.
 *
 * @returns {Promise<{ configured: boolean, running: boolean }>}
 */
export async function discordStatus() {
  const raw = await call("discord_status");
  return parseResponse(raw);
}

/**
 * Discord 봇을 시작한다.
 *
 * @returns {Promise<{ status: string }>}
 */
export async function discordStart() {
  const raw = await call("discord_start");
  return parseResponse(raw);
}

/**
 * Discord 봇을 정지한다 (sidecar에 명령이 없으면 graceful fail).
 *
 * @returns {Promise<{ status: string }>}
 */
export async function discordStop() {
  const raw = await call("discord_stop");
  return parseResponse(raw);
}

// ── Phase 1: Private-Claw — Telegram setup ───────────────────────────────────

/**
 * 텔레그램 봇 토큰을 설정하고 연결 테스트를 수행한다.
 *
 * @param {string} token - Telegram Bot API 토큰
 * @param {string} [chatId] - 허용할 chat_id (선택)
 * @returns {Promise<{ ok: boolean, bot_name: string, bot_username: string }>}
 */
export async function telegramSetup(token, chatId) {
  const raw = await call("telegram_setup", { token, chat_id: chatId ?? null });
  return parseResponse(raw);
}

// ── Sprint 5: 채팅 세션 영속화 ────────────────────────────────────────────────

export async function chatSaveMessage(sessionId, role, text, toolCalls, maskedCount, maskedTypes, errorText) {
  const raw = await call("chat_save_message", {
    session_id: sessionId,
    role,
    text: text ?? null,
    tool_calls: toolCalls ?? null,
    masked_count: maskedCount ?? 0,
    masked_types: maskedTypes ?? null,
    error_text: errorText ?? null,
  });
  return parseResponse(raw);
}

export async function chatListSessions(limit) {
  const raw = await call("chat_list_sessions", { limit: limit ?? null });
  return parseResponse(raw);
}

export async function chatGetMessages(sessionId) {
  const raw = await call("chat_get_messages", { session_id: sessionId });
  return parseResponse(raw);
}

export async function chatDeleteSession(sessionId) {
  const raw = await call("chat_delete_session", { session_id: sessionId });
  return parseResponse(raw);
}

// ── Sprint 5: 백업/내보내기 ───────────────────────────────────────────────────

export async function backupExport() {
  const raw = await call("backup_export");
  return parseResponse(raw);
}

export async function backupImport(filePath) {
  const raw = await call("backup_import", { file_path: filePath });
  return parseResponse(raw);
}

// ── Maintenance ────────────────────────────────────────────────────────────

/**
 * Delete all temporary files from excel_uploads/ and document_exports/.
 *
 * @returns {Promise<{ deleted_count: number, freed_bytes: number }>}
 */
export async function maintenanceCleanup() {
  const raw = await call("maintenance_cleanup");
  return parseResponse(raw);
}

// ── Phase 4: Agent / OpenClaw ─────────────────────────────────────────────

/**
 * OpenClaw 게이트웨이 현재 상태를 확인한다.
 *
 * @returns {Promise<{ state: 'running'|'stopped'|'error', port: number, message: string }>}
 */
export async function openclawStatus() {
  const raw = await call("openclaw_status");
  return parseResponse(raw);
}

/**
 * `openclaw` 바이너리가 시스템에 설치되어 있는지 확인한다.
 * 게이트웨이 실행 여부와 별개 — 자동 설치 UI를 띄울지 결정하는 용도.
 *
 * @returns {Promise<{ installed: boolean, version: string|null, source: 'path'|'login-shell'|null }>}
 */
export async function openclawInstalled() {
  const raw = await call("openclaw_installed");
  return parseResponse(raw);
}

/**
 * OpenClaw 게이트웨이가 18789에서 응답하도록 보장한다 (idempotent).
 * 이미 떠 있으면 즉시 OK, 아니면 자식 프로세스로 spawn 후 ready까지 대기.
 *
 * 자동 설치 모달이 npm install 완료 후 호출하여 즉시 온라인 전환.
 *
 * @returns {Promise<{ state: 'running'|'stopped'|'error', port: number, message: string }>}
 */
export async function openclawEnsureRunning() {
  const raw = await call("openclaw_ensure_running");
  return parseResponse(raw);
}

/**
 * OpenClaw config를 Ollama 프로바이더로 비인터랙티브 설정한다.
 * `models.providers.ollama.baseUrl` + `agents.defaults.model = ollama/<model>`을 set.
 *
 * @param {string} model — 예: "skt/A.X-4.0-Light:latest", "qwen3:8b" (provider prefix 없이)
 * @returns {Promise<{ ok: boolean, applied: Array, model: string }>}
 */
export async function openclawUseOllama(model) {
  const raw = await call("openclaw_use_ollama", { model });
  return parseResponse(raw);
}

/**
 * Ollama 종합 상태 — 바이너리 설치, 데몬 실행(11434), 설치된 모델 목록.
 *
 * @returns {Promise<{ installed: boolean, version: string|null, running: boolean, port: number, models: Array }>}
 */
export async function ollamaStatus() {
  const raw = await call("ollama_status");
  return parseResponse(raw);
}

// ── Installer commands ───────────────────────────────────────────────────────
//
// macOS GUI 앱의 PATH 제한($SHELL 미적용)을 우회하기 위해 Rust 측에서
// `$SHELL -lc "..."`로 실행하고, stdout/stderr를 `installer:log` Tauri 이벤트로
// 실시간 스트리밍한다. 프론트는 `listen("installer:log", ...)`로 받아 표시.
//
// 모든 함수의 반환 형태:
//   {
//     ok: boolean,
//     code: number|null,
//     stderr_tail: string[],   // stderr 마지막 N줄 (실패 컨텍스트)
//     eacces: boolean,         // 권한 오류 감지 → sudo 안내 UI로 분기
//     message: string,
//     manual_command: string,  // 사용자가 직접 실행할 명령 (실패 시 복사 제공)
//   }

/**
 * `npm install -g openclaw@latest` — 사용자 로그인 셸 경유.
 * @returns {Promise<object>} InstallResult
 */
export async function installerInstallOpenClaw() {
  // invoke는 직접 객체를 반환 (parseResponse 불필요 — Rust가 serde_json::Value로 반환)
  return invoke("install_openclaw");
}

/**
 * `brew install ollama` (macOS 전용).
 * @returns {Promise<object>} InstallResult
 */
export async function installerInstallOllama() {
  return invoke("install_ollama");
}

/**
 * `brew services start ollama`.
 * @returns {Promise<object>} InstallResult
 */
export async function installerStartOllama() {
  return invoke("start_ollama");
}

/**
 * `ollama pull <model>` — 모델명은 Rust에서 validate_model_name으로 사전 검증.
 * @param {string} model
 * @returns {Promise<object>} InstallResult
 */
export async function installerPullModel(model) {
  return invoke("pull_ollama_model", { model });
}

/** 진행 중인 설치 자식 프로세스를 kill한다. NO-OP if none. */
export async function installerCancel() {
  return invoke("cancel_install");
}

/**
 * OpenClaw 세션을 통해 AI 에이전트와 대화한다.
 *
 * 보안 레이어(Python sidecar)를 경유하여 OpenClaw 게이트웨이로 전달된다.
 * DENIED 키워드가 포함된 경우 사이드카에서 차단된다.
 *
 * @param {string} message - 사용자 메시지
 * @param {string|null} [sessionId] - 기존 세션 ID (null이면 새 세션 자동 생성)
 * @returns {Promise<{ response: string, session_id: string, tool_calls: Array }>}
 */
export async function agentChat(message, sessionId) {
  const raw = await call("agent_chat", {
    message,
    session_id: sessionId ?? null,
  });
  return parseResponse(raw);
}

/**
 * 활성 OpenClaw 세션 목록을 반환한다.
 *
 * @returns {Promise<{ sessions: Array }>}
 */
export async function agentSessions() {
  const raw = await call("agent_sessions");
  return parseResponse(raw);
}

/**
 * 설치된 OpenClaw 스킬 목록을 반환한다.
 *
 * @returns {Promise<{ skills: Array, count: number }>}
 */
export async function skillsInstalled() {
  const raw = await call("skills_installed");
  return parseResponse(raw);
}

/**
 * ClawHub에서 스킬을 설치한다.
 *
 * @param {string} skillName - ClawHub 스킬 이름 (예: "gog-gmail")
 * @returns {Promise<{ success: boolean, skill_name: string }>}
 */
export async function skillsInstall(skillName) {
  const raw = await call("skills_install", { skill_name: skillName });
  return parseResponse(raw);
}

/**
 * ClawHub 추천 스킬 카탈로그를 반환한다.
 *
 * @returns {Promise<{ skills: Array, cached: boolean }>}
 */
export async function skillsCatalog() {
  const raw = await call("skills_catalog");
  return parseResponse(raw);
}

// ── Phase 3 (2026-05): Rust keyring + audit (Python sidecar 우회 경로) ──────
//
// Python의 KeyringService/AuditService와 동일한 OS keychain + audit.jsonl
// 파일을 공유한다. 기존 storeCredential/getAuditLogs는 Python을 경유하지만
// 아래는 Rust가 OS에 직결 — Python 경로 가용성과 독립.

export async function rustCredentialSet(key, value) {
  return invoke("rust_credential_set", { key, value });
}

/** @returns {Promise<string|null>} */
export async function rustCredentialGet(key) {
  return invoke("rust_credential_get", { key });
}

export async function rustCredentialDelete(key) {
  return invoke("rust_credential_delete", { key });
}

/** @returns {Promise<string[]>} */
export async function rustCredentialList() {
  return invoke("rust_credential_list");
}

export async function rustAuditLog(action, target, detail) {
  return invoke("rust_audit_log", { action, target, detail: detail ?? null });
}

export async function rustAuditRecent(limit) {
  return invoke("rust_audit_recent", { limit: limit ?? null });
}

export async function rustAuditMaskingStats() {
  return invoke("rust_audit_masking_stats");
}

export async function rustAuditBlocked(limit) {
  return invoke("rust_audit_blocked", { limit: limit ?? null });
}

export async function rustAuditLastBlockedAt() {
  return invoke("rust_audit_last_blocked_at");
}

// ── OpenClaw CLI 서브프로세스 wrapper (2026-05-20) ──────────────────────────
//
// `openclaw gateway call <method>` / `openclaw agent`를 spawn해 결과 JSON을 반환.
// WebSocket 직결 대신 OpenClaw 자체 CLI에 핸드셰이크/auth/세션을 위임 — 게이트웨이
// 마이너 버전이 바뀌어도 우리 wrapper는 영향받지 않는다.
//
// 사용자 셋업 책임: Ollama 프로바이더 등록 (`openclaw configure` 또는
// `OLLAMA_API_KEY=*`), device pairing 등은 별도. 이 wrapper는 그저 CLI를 부른다.

/**
 * 게이트웨이 메서드 호출 — health / system-presence / cron.* 등.
 *
 * @param {string} method
 * @param {object|null} [params] — `--params` JSON
 * @param {{ token?:string, password?:string, url?:string, timeoutMs?:number, expectFinal?:boolean }} [opts]
 * @returns {Promise<any>} stdout JSON
 */
export async function openclawCliCall(method, params, opts) {
  return invoke("openclaw_cli_call", {
    method,
    params: params ?? null,
    opts: opts
      ? {
          token: opts.token ?? null,
          password: opts.password ?? null,
          url: opts.url ?? null,
          timeout_ms: opts.timeoutMs ?? null,
          expect_final: !!opts.expectFinal,
        }
      : null,
  });
}

/**
 * 에이전트 한 턴 실행 — 메신저 봇이 받은 메시지를 게이트웨이로 전달할 때 사용.
 *
 * @param {{
 *   message: string,
 *   agent?: string,
 *   sessionId?: string,
 *   to?: string,
 *   channel?: string,
 *   model?: string,
 *   deliver?: boolean,
 *   opts?: object,
 * }} req
 */
export async function openclawCliAgent(req) {
  return invoke("openclaw_cli_agent", {
    req: {
      message: req.message,
      agent: req.agent ?? null,
      session_id: req.sessionId ?? null,
      to: req.to ?? null,
      channel: req.channel ?? null,
      model: req.model ?? null,
      deliver: !!req.deliver,
      opts: req.opts
        ? {
            token: req.opts.token ?? null,
            password: req.opts.password ?? null,
            url: req.opts.url ?? null,
            timeout_ms: req.opts.timeoutMs ?? null,
            expect_final: !!req.opts.expectFinal,
          }
        : { token: null, password: null, url: null, timeout_ms: null, expect_final: false },
    },
  });
}

