//! OS 자격증명 저장소 (Phase 3 — 2026-05).
//!
//! Python sidecar의 `services/keyring_service.py`를 Rust로 포팅한 평행 경로.
//! 동일한 `SERVICE_NAMESPACE = "office_claw"`를 쓰고, `credentials_registry.json`도
//! Python과 같은 위치에 쓴다 — Python·Rust 어느 쪽에서 저장해도 다른 쪽이 즉시
//! 읽을 수 있다.
//!
//! OS 백엔드:
//!   - macOS: Keychain (Security framework)
//!   - Windows: Credential Manager
//!   - Linux: Secret Service (gnome-keyring / KWallet)
//!
//! 모듈명을 `keyring`이 아닌 `keyring_svc`로 둔 이유: `keyring` crate 자체와
//! 충돌 회피.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

pub const SERVICE_NAMESPACE: &str = "office_claw";

#[derive(Debug, Serialize, Deserialize, Default)]
struct Registry {
    keys: Vec<String>,
}

fn registry_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("data dir 조회 실패: {}", e))?;
    Ok(dir.join("credentials_registry.json"))
}

fn load_registry(app: &AppHandle) -> Vec<String> {
    let Ok(path) = registry_path(app) else {
        return Vec::new();
    };
    let Ok(data) = std::fs::read_to_string(&path) else {
        return Vec::new();
    };
    serde_json::from_str::<Registry>(&data)
        .map(|r| r.keys)
        .unwrap_or_default()
}

fn save_registry(app: &AppHandle, keys: Vec<String>) -> Result<(), String> {
    let path = registry_path(app)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("디렉터리 생성 실패: {}", e))?;
    }
    // Python과 동일하게 dedupe + sort
    let mut deduped: Vec<String> = keys
        .into_iter()
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    deduped.sort();
    let body = serde_json::to_string_pretty(&Registry { keys: deduped })
        .map_err(|e| format!("registry 직렬화 실패: {}", e))?;
    std::fs::write(&path, body).map_err(|e| format!("registry 쓰기 실패: {}", e))?;
    Ok(())
}

/// OS keychain에 자격증명 저장 + 로컬 레지스트리에 key 등록.
pub fn store(app: &AppHandle, key: &str, value: &str) -> Result<(), String> {
    let entry = keyring::Entry::new(SERVICE_NAMESPACE, key)
        .map_err(|e| format!("keyring Entry 생성 실패: {}", e))?;
    entry
        .set_password(value)
        .map_err(|e| format!("keyring 저장 실패: {}", e))?;

    let mut keys = load_registry(app);
    if !keys.contains(&key.to_string()) {
        keys.push(key.to_string());
        save_registry(app, keys)?;
    }

    crate::audit::log(app, "credential_store", key, "");
    Ok(())
}

/// OS keychain에서 자격증명 조회. 없으면 None.
pub fn retrieve(app: &AppHandle, key: &str) -> Result<Option<String>, String> {
    let entry = keyring::Entry::new(SERVICE_NAMESPACE, key)
        .map_err(|e| format!("keyring Entry 생성 실패: {}", e))?;
    crate::audit::log(app, "credential_retrieve", key, "");
    match entry.get_password() {
        Ok(v) => Ok(Some(v)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("keyring 조회 실패: {}", e)),
    }
}

/// OS keychain에서 자격증명 삭제 + 레지스트리에서 key 제거.
pub fn delete(app: &AppHandle, key: &str) -> Result<(), String> {
    let entry = keyring::Entry::new(SERVICE_NAMESPACE, key)
        .map_err(|e| format!("keyring Entry 생성 실패: {}", e))?;
    match entry.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => {}
        Err(e) => return Err(format!("keyring 삭제 실패: {}", e)),
    }

    let keys: Vec<String> = load_registry(app)
        .into_iter()
        .filter(|k| k != key)
        .collect();
    save_registry(app, keys)?;

    crate::audit::log(app, "credential_delete", key, "");
    Ok(())
}

/// 저장된 key 이름 목록 (value는 절대 반환하지 않음).
pub fn list_keys(app: &AppHandle) -> Vec<String> {
    load_registry(app)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_serializes_sorted_unique() {
        let r = Registry {
            keys: vec!["b".into(), "a".into(), "a".into()],
        };
        let json = serde_json::to_string(&r).unwrap();
        // dedupe/sort는 save_registry에서 일어남 — 여기는 raw 직렬화만 검증
        assert!(json.contains("\"a\""));
        assert!(json.contains("\"b\""));
    }

    #[test]
    fn service_namespace_is_stable() {
        // Python config.py와 동일해야 한다 — 이 값 변경 시 양쪽이 다른 OS keychain을 보게 된다.
        assert_eq!(SERVICE_NAMESPACE, "office_claw");
    }
}
