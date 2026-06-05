use tauri::{AppHandle, Manager};

pub fn show_panel(app: &AppHandle) -> Result<(), String> {
    let panel = app
        .get_webview_window("panel")
        .ok_or_else(|| "panel window not found".to_string())?;
    panel.show().map_err(|err| err.to_string())?;
    panel.set_focus().map_err(|err| err.to_string())?;
    Ok(())
}

pub fn hide_panel(app: &AppHandle) -> Result<(), String> {
    let panel = app
        .get_webview_window("panel")
        .ok_or_else(|| "panel window not found".to_string())?;
    panel.hide().map_err(|err| err.to_string())
}

pub fn place_bubble(app: &AppHandle) {
    if let Some(bubble) = app.get_webview_window("bubble") {
        let _ = bubble.set_always_on_top(true);
    }
    if let Some(panel) = app.get_webview_window("panel") {
        let _ = panel.set_always_on_top(true);
    }
}
