mod audit;
mod installer;
mod ipc;
mod keyring_svc;
mod ollama;
mod shell;
mod sidecar;
mod tray;

use tauri::Manager;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        // Sprint 5: 자동 업데이트 플러그인
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            // Installer state — 진행 중인 설치 자식 프로세스 핸들 (cancel용).
            app.manage(std::sync::Mutex::new(installer::InstallerState::default()));

            // Python sidecar 기동
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                match sidecar::spawn_sidecar(&app_handle).await {
                    Ok(()) => println!("[office-claw] Sidecar started successfully"),
                    Err(e) => eprintln!("[office-claw] Failed to start sidecar: {}", e),
                }
            });

            // Setup system tray
            tray::setup_tray(app)?;

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            ipc::health_check,
            ipc::store_credential,
            ipc::get_credential,
            ipc::delete_credential,
            ipc::list_credentials,
            ipc::chat,
            ipc::get_audit_logs,
            ipc::telegram_status,
            ipc::telegram_start,
            ipc::telegram_stop,
            ipc::get_llm_settings,
            ipc::save_llm_settings,
            ipc::excel_live_status,
            ipc::excel_live_command,
            ipc::excel_live_submit_approval,
            ipc::excel_live_save_workbook,
            ipc::maintenance_cleanup,
            // Ollama 로컬 LLM 상태
            ipc::ollama_status,
            // Phase 3 (2026-05): Rust keyring + audit — Python과 같은 저장소 공유
            ipc::rust_credential_set,
            ipc::rust_credential_get,
            ipc::rust_credential_delete,
            ipc::rust_credential_list,
            ipc::rust_audit_log,
            ipc::rust_audit_recent,
            ipc::rust_audit_masking_stats,
            ipc::rust_audit_blocked,
            ipc::rust_audit_last_blocked_at,
            // Installer: macOS GUI PATH 우회 + 실시간 로그 스트리밍
            installer::install_ollama,
            installer::start_ollama,
            installer::pull_ollama_model,
            installer::cancel_install,
            // Phase 5: Security — Dashboard
            ipc::security_stats,
            ipc::security_blocked_log,
            ipc::security_get_whitelist,
            ipc::security_update_whitelist,
            ipc::security_get_masking_settings,
            ipc::security_update_masking_settings,
            // Phase 2: Command Audit Log
            ipc::command_audit_list,
            ipc::command_audit_stats,
            ipc::command_audit_clear,
            // Phase 2: Security UI Approval (텔레그램 미연결 시 앱 UI 대체 수단)
            ipc::security_get_pending_approvals,
            ipc::security_respond_approval,
            // Phase 3: Slack commands
            ipc::slack_setup,
            ipc::slack_status,
            ipc::slack_start,
            ipc::slack_stop,
            // Phase 3: Discord commands
            ipc::discord_setup,
            ipc::discord_status,
            ipc::discord_start,
            ipc::discord_stop,
            // Phase 3: Permissions commands
            ipc::permissions_get,
            ipc::permissions_update,
            ipc::permissions_whitelist_add,
            ipc::permissions_whitelist_remove,
            // Phase 1: officeclaw — Workspace + Telegram setup
            ipc::open_workspace_folder,
            ipc::open_workspace_file,
            ipc::workspace_list_files,
            ipc::workspace_read_file,
            ipc::workspace_write_file,
            ipc::workspace_create_excel_file,
            ipc::workspace_write_file_binary, // Sprint 3: 바이너리 업로드 (S-2 해소)
            ipc::telegram_setup,
            // Sprint 5: 채팅 세션 영속화
            ipc::chat_save_message,
            ipc::chat_list_sessions,
            ipc::chat_get_messages,
            ipc::chat_delete_session,
            // Sprint 5: 백업/내보내기
            ipc::backup_export,
            ipc::backup_import,
            // Sprint 5: 자동 업데이트
            ipc::check_for_update,
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Hide window instead of closing (tray app behavior)
                window.hide().unwrap_or_default();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
