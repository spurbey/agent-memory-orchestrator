use std::{fs, process};

use crate::config;

pub fn write_pid() -> Result<(), String> {
    let path = config::pid_path();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    fs::write(path, format!("{{\"pid\":{}}}\n", process::id())).map_err(|err| err.to_string())
}

pub fn clear_pid() {
    let path = config::pid_path();
    let _ = fs::remove_file(path);
}
