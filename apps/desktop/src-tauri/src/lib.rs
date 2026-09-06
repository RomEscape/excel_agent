mod audit;
mod installer;
mod ipc;
mod keyring_svc;
mod ollama;
mod openclaw;
mod openclaw_cli;
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
            // Register OpenClaw state (before spawning so IPC commands can access it)
            app.manage(std::sync::Mutex::new(openclaw::OpenClawState::default()));

            // Installer state — 진행 중인 설치 자식 프로세스 핸들 (cancel용).
            app.manage(std::sync::Mutex::new(installer::InstallerState::default()));

            // 사이드카를 먼저, OpenClaw 게이트웨이는 그다음에 띄운다.
            //
            // 예전엔 게이트웨이가 먼저였는데, `openclaw` CLI 가 없는 PC 에서는 30초 타임아웃을
            // **다 기다린 뒤에야** 사이드카를 띄웠다. 그동안 온보딩 3단계(워크스페이스)는
            // 사이드카에 경로를 물어보다 30초 만에 포기해 빈 칸 + 비활성 확인 버튼으로 남았다
            // (2026-09-06 새 PC 첫 구동 실측). 둘은 서로 의존하지 않는다 — 게이트웨이는
            // 없어도 앱이 돌고, 사이드카는 게이트웨이 없이도 Ollama 를 직접 쓴다.
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                // Step 1: Start Python sidecar
                match sidecar::spawn_sidecar(&app_handle).await {
                    Ok(()) => println!("[office-claw] Sidecar started successfully"),
                    Err(e) => eprintln!("[office-claw] Failed to start sidecar: {}", e),
                }

                // Step 2: Start OpenClaw gateway (optional)
                let openclaw_state =
                    app_handle.state::<std::sync::Mutex<openclaw::OpenClawState>>();
                match openclaw::spawn_openclaw(&openclaw_state).await {
                    Ok(()) => println!("[office-claw] OpenClaw gateway started"),
                    Err(e) => eprintln!("[office-claw] OpenClaw gateway unavailable: {}", e),
                    // Not fatal — app continues, Python sidecar may fall back to direct LLM
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
            ipc::get_llm_settings,
            ipc::save_llm_settings,
            ipc::excel_upload,
            ipc::excel_analyze,
            ipc::excel_report,
            ipc::excel_formulas,
            ipc::excel_chart_data,
            ipc::excel_export,
            ipc::excel_live_status,
            ipc::excel_live_selection,
            ipc::excel_live_command,
            ipc::trace_client_event,
            ipc::excel_live_macro_step,
            ipc::excel_live_macro_abort,
            ipc::excel_live_submit_approval,
            ipc::excel_live_save_workbook,
            ipc::excel_live_select_workbook, // 2026-09-06: 목록 클릭으로 대상 통합문서 선택
            ipc::excel_live_preview,         // 2026-09-06: 앱 안 통합문서 미리보기
            ipc::excel_live_list_backups,
            ipc::excel_live_restore_last_backup,
            ipc::harness_feedback,
            ipc::harness_replay_failures,
            ipc::harness_personalization,
            ipc::document_generate,
            ipc::document_export_docx,
            ipc::document_export_pdf,
            ipc::maintenance_cleanup,
            // Phase 4: OpenClaw / Agent commands
            ipc::agent_chat,
            ipc::agent_sessions,
            ipc::openclaw_status,
            ipc::openclaw_installed,
            ipc::openclaw_ensure_running,
            ipc::openclaw_use_ollama,
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
            // OpenClaw CLI 서브프로세스 wrapper (2026-05-20)
            ipc::openclaw_cli_call,
            ipc::openclaw_cli_agent,
            // Installer: macOS GUI PATH 우회 + 실시간 로그 스트리밍
            installer::install_ollama,
            installer::install_openclaw,
            installer::start_ollama,
            installer::pull_ollama_model,
            installer::cancel_install,
            ipc::skills_installed,
            ipc::skills_install,
            ipc::skills_catalog,
            // Phase 5: Security — Approval
            ipc::agent_submit_approval,
            ipc::agent_pending_approvals,
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
            // Phase 3: Permissions commands
            ipc::permissions_get,
            ipc::permissions_update,
            ipc::permissions_whitelist_add,
            ipc::permissions_whitelist_remove,
            // Phase 1: officeclaw — Workspace
            ipc::open_workspace_folder,
            ipc::open_workspace_file,
            ipc::workspace_list_files,
            ipc::workspace_read_file,
            ipc::workspace_write_file,
            ipc::workspace_delete_file,
            ipc::workspace_create_excel_file,
            ipc::workspace_write_file_binary, // Sprint 3: 바이너리 업로드 (S-2 해소)
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
            // 모바일 릴레이(중계 서버) 연동 — QR 페어링
            ipc::relay_pair,
            ipc::relay_status,
            ipc::relay_disconnect,
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
