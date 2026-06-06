use std::{
    net::{SocketAddr, TcpStream},
    process::Command,
    sync::{Mutex, OnceLock},
    time::{Duration, Instant},
};

use crate::config;

const SPAWN_COOLDOWN: Duration = Duration::from_secs(8);

pub fn ensure_daemon_started() -> Result<(), String> {
    if daemon_reachable() || !spawn_allowed() {
        return Ok(());
    }
    let Some(daemon) = config::daemon_command() else {
        return Ok(());
    };
    let mut command = Command::new(daemon.program);
    command.args(daemon.args);
    command.env("AMO_HOME", config::amo_home());
    hide_console(&mut command);
    command.spawn().map_err(|err| err.to_string())?;
    Ok(())
}

fn spawn_allowed() -> bool {
    static LAST_SPAWN: OnceLock<Mutex<Option<Instant>>> = OnceLock::new();
    let mut last = LAST_SPAWN.get_or_init(|| Mutex::new(None)).lock().ok();
    let Some(ref mut last) = last else {
        return false;
    };
    if let Some(when) = **last {
        if when.elapsed() < SPAWN_COOLDOWN {
            return false;
        }
    }
    **last = Some(Instant::now());
    true
}

fn daemon_reachable() -> bool {
    let base_url = config::daemon_base_url();
    let host_port = base_url
        .trim_start_matches("http://")
        .trim_start_matches("https://")
        .split('/')
        .next()
        .unwrap_or("127.0.0.1:8765");
    let address: SocketAddr = host_port
        .parse()
        .unwrap_or_else(|_| "127.0.0.1:8765".parse().expect("valid fallback address"));
    TcpStream::connect_timeout(&address, Duration::from_millis(350)).is_ok()
}

#[cfg(target_os = "windows")]
fn hide_console(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(target_os = "windows"))]
fn hide_console(_: &mut Command) {}
