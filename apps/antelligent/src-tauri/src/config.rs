use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};
use std::{env, fs, path::PathBuf};

#[derive(Clone, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct LaunchConfig {
    pub amo_home: Option<String>,
    pub daemon_url: Option<String>,
    pub daemon_command: Option<DaemonCommand>,
    pub ui_token_path: Option<String>,
}

#[derive(Clone, Deserialize)]
pub struct DaemonCommand {
    pub program: String,
    #[serde(default)]
    pub args: Vec<String>,
}

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

pub fn launch_config() -> Option<LaunchConfig> {
    let path = launch_config_path();
    let content = fs::read_to_string(path).ok()?;
    serde_json::from_str(&content).ok()
}

pub fn daemon_base_url() -> String {
    launch_config()
        .and_then(|config| config.daemon_url)
        .filter(|value| !value.trim().is_empty())
        .or_else(|| non_empty_env("ANTELLIGENT_DAEMON_URL"))
        .or_else(|| non_empty_env("AMO_DAEMON_URL"))
        .unwrap_or_else(|| "http://127.0.0.1:8765".to_string())
}

pub fn daemon_command() -> Option<DaemonCommand> {
    launch_config().and_then(|config| config.daemon_command)
}

pub fn amo_home() -> PathBuf {
    if let Some(value) = launch_config().and_then(|config| config.amo_home) {
        if !value.trim().is_empty() {
            return PathBuf::from(value);
        }
    }
    if let Some(value) = non_empty_env("AMO_HOME") {
        return PathBuf::from(value);
    }
    home_dir().join(".agent-memory-orchestrator")
}

pub fn launch_config_path() -> PathBuf {
    if let Some(value) = non_empty_env("ANTELLIGENT_CONFIG") {
        return PathBuf::from(value);
    }
    if let Some(value) = non_empty_env("AMO_HOME") {
        return PathBuf::from(value)
            .join(".ui")
            .join("antelligent.launch.json");
    }
    home_dir()
        .join(".agent-memory-orchestrator")
        .join(".ui")
        .join("antelligent.launch.json")
}

pub fn token_path() -> PathBuf {
    if let Some(value) = launch_config().and_then(|config| config.ui_token_path) {
        if !value.trim().is_empty() {
            return PathBuf::from(value);
        }
    }
    amo_home().join(".ui").join("antelligent.token")
}

pub fn pid_path() -> PathBuf {
    amo_home()
        .join("apps")
        .join("antelligent")
        .join("antelligent.pid")
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

fn non_empty_env(name: &str) -> Option<String> {
    env::var(name).ok().filter(|value| !value.trim().is_empty())
}
