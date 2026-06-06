use tauri::{AppHandle, Manager, PhysicalPosition, PhysicalSize, Position, Size, WebviewWindow};

pub fn show_panel(app: &AppHandle) -> Result<(), String> {
    let panel = app
        .get_webview_window("panel")
        .ok_or_else(|| "panel window not found".to_string())?;
    let bubble = app.get_webview_window("bubble");
    position_panel_from_bubble(&panel, bubble.as_ref());
    panel.show().map_err(|err| err.to_string())?;
    panel.set_focus().map_err(|err| err.to_string())?;
    play_panel_open(&panel);
    if let Some(bubble) = bubble {
        let _ = bubble.hide();
    }
    Ok(())
}

pub fn hide_panel(app: &AppHandle) -> Result<(), String> {
    let panel = app
        .get_webview_window("panel")
        .ok_or_else(|| "panel window not found".to_string())?;
    if let Some(bubble) = app.get_webview_window("bubble") {
        let _ = bubble.show();
        let _ = bubble.set_focus();
    }
    panel.hide().map_err(|err| err.to_string())
}

pub fn place_bubble(app: &AppHandle) {
    if let Some(bubble) = app.get_webview_window("bubble") {
        let _ = bubble.set_always_on_top(true);
        if let Ok(Some(monitor)) = bubble.current_monitor() {
            if let Ok(size) = bubble.outer_size() {
                let area = monitor.work_area();
                let margin = 18_i32;
                let x = area.position.x + area.size.width as i32 - size.width as i32 - margin;
                let y = area.position.y + margin;
                let _ = bubble.set_position(Position::Physical(PhysicalPosition { x, y }));
            }
        }
    }
    if let Some(panel) = app.get_webview_window("panel") {
        let _ = panel.set_always_on_top(true);
    }
}

fn position_panel_from_bubble(panel: &WebviewWindow, bubble: Option<&WebviewWindow>) {
    let monitor = bubble
        .and_then(|window| window.current_monitor().ok().flatten())
        .or_else(|| panel.current_monitor().ok().flatten());
    let Some(monitor) = monitor else {
        return;
    };
    let area = monitor.work_area();
    let margin = 14_i32;
    let panel_width = (area.size.width / 4).max(340).min(area.size.width);
    let panel_height = area.size.height;
    let _ = panel.set_size(Size::Physical(PhysicalSize {
        width: panel_width,
        height: panel_height,
    }));
    let anchor_x = bubble
        .and_then(|window| window.outer_position().ok())
        .map(|position| position.x)
        .unwrap_or(area.position.x + area.size.width as i32 - panel_width as i32 - margin);
    let min_x = area.position.x + margin;
    let max_x = area.position.x + area.size.width as i32 - panel_width as i32 - margin;
    let x = (anchor_x - 20).clamp(min_x, max_x.max(min_x));
    let y = area.position.y;
    let _ = panel.set_position(Position::Physical(PhysicalPosition { x, y }));
}

fn play_panel_open(panel: &WebviewWindow) {
    let _ = panel.eval(
        "const el=document.querySelector('.app-window');if(el){el.classList.remove('closing');el.classList.add('opening');setTimeout(()=>el.classList.remove('opening'),240);}",
    );
}
