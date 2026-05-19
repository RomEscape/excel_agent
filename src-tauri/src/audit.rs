//! 로컬 감사 로그 JSONL writer/reader (Phase 3 — 2026-05).
//!
//! Python sidecar의 `services/audit_service.py`를 Rust로 포팅한 평행 경로.
//! 동일한 `audit.jsonl` 파일을 공유 — Python·Rust 어느 쪽이 append해도 다른
//! 쪽이 read한다. POSIX `O_APPEND`로 append하는 write는 PIPE_BUF 이하 단일
//! write 시 atomic하다 — 한 entry가 평균 200B 내외라 안전 범위.
//!
//! 스키마(Python과 동일):
//!   { "timestamp": ISO8601, "action": "...", "target": "...", "detail": "..." }

use chrono::{DateTime, Datelike, TimeZone, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::io::Write;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    pub timestamp: String,
    pub action: String,
    pub target: String,
    pub detail: String,
}

pub fn log_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("data dir 조회 실패: {}", e))?;
    Ok(dir.join("audit.jsonl"))
}

/// 감사 로그 한 줄 append. 실패는 무시(보조 경로) — eprintln만.
pub fn log(app: &AppHandle, action: &str, target: &str, detail: &str) {
    let entry = AuditEntry {
        timestamp: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Micros, true),
        action: action.to_string(),
        target: target.to_string(),
        detail: detail.to_string(),
    };
    let Ok(path) = log_path(app) else {
        eprintln!("[audit] 경로 조회 실패 — skip");
        return;
    };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    else {
        eprintln!("[audit] 파일 열기 실패: {}", path.display());
        return;
    };
    let line = match serde_json::to_string(&entry) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("[audit] 직렬화 실패: {}", e);
            return;
        }
    };
    if let Err(e) = writeln!(f, "{}", line) {
        eprintln!("[audit] 쓰기 실패: {}", e);
    }
}

fn read_all(app: &AppHandle) -> Vec<AuditEntry> {
    let Ok(path) = log_path(app) else {
        return Vec::new();
    };
    let Ok(data) = std::fs::read_to_string(&path) else {
        return Vec::new();
    };
    data.lines()
        .filter(|l| !l.trim().is_empty())
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect()
}

/// 최근 entry를 limit개 반환 (Python: reversed(entries[-limit:])).
pub fn get_logs(app: &AppHandle, limit: usize) -> Vec<AuditEntry> {
    let all = read_all(app);
    let start = all.len().saturating_sub(limit);
    let mut tail: Vec<AuditEntry> = all.into_iter().skip(start).collect();
    tail.reverse();
    tail
}

/// 마스킹 통계 — today / week / total로 유형별 카운트.
/// detail 포맷: "N건 마스킹: 유형1, 유형2"
#[derive(Debug, Serialize, Deserialize, Default)]
pub struct MaskingStats {
    pub today: BTreeMap<String, u64>,
    pub week: BTreeMap<String, u64>,
    pub total: BTreeMap<String, u64>,
}

pub fn masking_stats(app: &AppHandle) -> MaskingStats {
    let now = Utc::now();
    let today_start = Utc
        .with_ymd_and_hms(now.year(), now.month(), now.day(), 0, 0, 0)
        .single()
        .unwrap_or(now);
    let week_start = today_start - chrono::Duration::days(7);

    let mut stats = MaskingStats::default();
    for e in read_all(app) {
        if e.action != "masking.applied" {
            continue;
        }
        let Some(idx) = e.detail.find("마스킹:") else {
            continue;
        };
        let types_part = &e.detail[idx + "마스킹:".len()..];
        let types: Vec<&str> = types_part.split(',').map(|s| s.trim()).collect();

        let ts: Option<DateTime<Utc>> = DateTime::parse_from_rfc3339(&e.timestamp)
            .ok()
            .map(|d| d.with_timezone(&Utc));

        for t in types {
            if t.is_empty() {
                continue;
            }
            *stats.total.entry(t.to_string()).or_insert(0) += 1;
            if let Some(ts) = ts {
                if ts >= week_start {
                    *stats.week.entry(t.to_string()).or_insert(0) += 1;
                }
                if ts >= today_start {
                    *stats.today.entry(t.to_string()).or_insert(0) += 1;
                }
            }
        }
    }
    stats
}

const BLOCK_ACTIONS: &[&str] = &[
    "agent.chat.denied",
    "approval.rejected",
    "approval.auto_rejected",
];

/// 차단 이벤트 최신순 limit개.
pub fn get_blocked_log(app: &AppHandle, limit: usize) -> Vec<AuditEntry> {
    let all = read_all(app);
    let blocked: Vec<AuditEntry> = all
        .into_iter()
        .filter(|e| BLOCK_ACTIONS.contains(&e.action.as_str()))
        .collect();
    let start = blocked.len().saturating_sub(limit);
    let mut tail: Vec<AuditEntry> = blocked.into_iter().skip(start).collect();
    tail.reverse();
    tail
}

pub fn last_blocked_at(app: &AppHandle) -> Option<String> {
    read_all(app)
        .into_iter()
        .rev()
        .find(|e| BLOCK_ACTIONS.contains(&e.action.as_str()))
        .map(|e| e.timestamp)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn block_actions_match_python() {
        // Python audit_service.py와 동일한 액션 셋
        assert_eq!(BLOCK_ACTIONS.len(), 3);
        assert!(BLOCK_ACTIONS.contains(&"agent.chat.denied"));
        assert!(BLOCK_ACTIONS.contains(&"approval.rejected"));
        assert!(BLOCK_ACTIONS.contains(&"approval.auto_rejected"));
    }

    #[test]
    fn entry_serializes_with_python_compatible_keys() {
        let e = AuditEntry {
            timestamp: "2026-05-20T00:00:00Z".into(),
            action: "credential_store".into(),
            target: "telegram_token".into(),
            detail: "".into(),
        };
        let json = serde_json::to_string(&e).unwrap();
        assert!(json.contains("\"timestamp\""));
        assert!(json.contains("\"action\""));
        assert!(json.contains("\"target\""));
        assert!(json.contains("\"detail\""));
    }

    /// Python detail 포맷에서 유형 추출 sanity check.
    #[test]
    fn extracts_types_from_detail() {
        let detail = "3건 마스킹: 주민등록번호, 이메일 주소";
        let idx = detail.find("마스킹:").unwrap();
        let rest = &detail[idx + "마스킹:".len()..];
        let types: Vec<&str> = rest.split(',').map(|s| s.trim()).collect();
        assert_eq!(types, vec!["주민등록번호", "이메일 주소"]);
    }
}
