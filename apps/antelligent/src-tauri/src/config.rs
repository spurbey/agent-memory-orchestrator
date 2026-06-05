use rand::{rngs::OsRng, RngCore};
use serde::Serialize;
use std::{env, fs, path::PathBuf};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendInfo {
    pub base_url: String,
    pub token: String,
}

pub fn backend_info() -> Result<BackendInfo, String> {
    Ok(BackendInfo {
        base_url: daemon_base_url(),
        token: ensure_token()?,
    })
}

pub fn daemon_base_url() -> String {
    env::var("ANTELLIGENT_DAEMON_URL")
        .or_else(|_| env::var("AMO_DAEMON_URL"))
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string())
}

pub fn amo_home() -> PathBuf {
    if let Ok(value) = env::var("AMO_HOME") {
        return PathBuf::from(value);
    }
    home_dir().join(".agent-memory-orchestrator")
}

pub fn token_path() -> PathBuf {
    amo_home().join(".ui").join("antelligent.token")
}

fn ensure_token() -> Result<String, String> {
    let path = token_path();
    if let Ok(token) = fs::read_to_string(&path) {
        let trimmed = token.trim().to_string();
        if !trimmed.is_empty() {
            return Ok(trimmed);
        }
    }
    let mut bytes = [0_u8; 32];
    OsRng.fill_bytes(&mut bytes);
    let token = bytes
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    fs::write(path, format!("{token}\n")).map_err(|err| err.to_string())?;
    Ok(token)
}

fn home_dir() -> PathBuf {
    env::var("USERPROFILE")
        .or_else(|_| env::var("HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}
