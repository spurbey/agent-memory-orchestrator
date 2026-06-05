mod config;
mod supervisor;
mod tray;
mod window;

use config::BackendInfo;

#[tauri::command]
fn backend_info() -> Result<BackendInfo, String> {
    config::backend_info()
}

#[tauri::command]
fn show_panel(app: tauri::AppHandle) -> Result<(), String> {
    window::show_panel(&app)
}

#[tauri::command]
fn hide_panel(app: tauri::AppHandle) -> Result<(), String> {
    window::hide_panel(&app)
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            backend_info,
            show_panel,
            hide_panel
        ])
        .setup(|app| {
            supervisor::ensure_daemon_started();
            tray::install(app.handle())?;
            window::place_bubble(app.handle());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("failed to run Antelligent");
}
