use std::{
    net::{SocketAddr, TcpStream},
    process::Command,
    time::Duration,
};

use crate::config;

pub fn ensure_daemon_started() {
    if daemon_reachable() {
        return;
    }
    let mut command = Command::new("amo-daemon");
    command.env("AMO_HOME", config::amo_home());
    hide_console(&mut command);
    let _ = command.spawn();
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
