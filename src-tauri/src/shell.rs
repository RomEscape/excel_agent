//! 로그인 셸 명령 실행 헬퍼 — GUI 앱 PATH 우회 공용 모듈.
//!
//! Tauri GUI 앱은 launchd/Finder가 주는 최소 PATH만 상속받아 Homebrew 등이
//! 설치한 바이너리를 못 찾는다. 사용자 로그인 셸을 `-lc`로 실행하면 ~/.zshrc 등
//! 셸 초기화 스크립트를 거쳐 PATH가 올바르게 잡힌다.
//!   - Unix: `$SHELL -lc "<cmd>"` (SHELL 미설정 시 `/bin/sh`)
//!   - Windows: `powershell -NoProfile -Command "<cmd>"`
//!
//! ollama.rs가 사용한다. installer.rs는 stdout/stderr 파이프 +
//! 라인 스트리밍 + PID 추적이 필요해 별도 구현(run_shell_streaming)을 유지한다.

use std::process::{Command, Output};

/// 로그인 셸을 통해 명령을 실행하고 완료까지 대기한 뒤 `Output`을 반환한다.
pub fn run_login_shell(cmd: &str) -> std::io::Result<Output> {
    #[cfg(target_os = "windows")]
    {
        Command::new("powershell")
            .args(["-NoProfile", "-Command", cmd])
            .output()
    }
    #[cfg(not(target_os = "windows"))]
    {
        let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".to_string());
        Command::new(shell).args(["-lc", cmd]).output()
    }
}
