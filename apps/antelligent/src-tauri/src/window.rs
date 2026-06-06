use tauri::{
    window::Color, AppHandle, Manager, PhysicalPosition, PhysicalSize, Position, Size,
    WebviewWindow,
};

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
        reset_bubble_visual(&bubble);
        let _ = bubble.show();
        let _ = bubble.set_focus();
    }
    panel.hide().map_err(|err| err.to_string())?;
    reset_panel_visual(&panel);
    Ok(())
}

pub fn place_bubble(app: &AppHandle) {
    if let Some(bubble) = app.get_webview_window("bubble") {
        let _ = bubble.set_always_on_top(true);
        let _ = bubble.set_shadow(false);
        let _ = bubble.set_background_color(Some(Color(0, 0, 0, 0)));
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
    let panel_width = (area.size.width * 3 / 10).max(420).min(area.size.width);
    let panel_height = (area.size.height * 3 / 5).max(520).min(area.size.height);
    let _ = panel.set_size(Size::Physical(PhysicalSize {
        width: panel_width,
        height: panel_height,
    }));
    let (anchor_x, anchor_y) = bubble
        .and_then(|window| window.outer_position().ok())
        .map(|position| (position.x, position.y))
        .unwrap_or((
            area.position.x + area.size.width as i32 - panel_width as i32 - margin,
            area.position.y + margin,
        ));
    let min_x = area.position.x + margin;
    let max_x = area.position.x + area.size.width as i32 - panel_width as i32 - margin;
    let min_y = area.position.y + margin;
    let max_y = area.position.y + area.size.height as i32 - panel_height as i32 - margin;
    let x = (anchor_x - 20).clamp(min_x, max_x.max(min_x));
    let y = (anchor_y - 20).clamp(min_y, max_y.max(min_y));
    let _ = panel.set_position(Position::Physical(PhysicalPosition { x, y }));
}

fn play_panel_open(panel: &WebviewWindow) {
    let _ = panel.eval(
        "const el=document.querySelector('.app-window');if(el){el.classList.remove('closing');el.classList.add('opening');setTimeout(()=>el.classList.remove('opening'),240);}",
    );
}

fn reset_panel_visual(panel: &WebviewWindow) {
    let _ = panel.eval(
        "const el=document.querySelector('.app-window');if(el){el.classList.remove('closing','opening');}",
    );
}

fn reset_bubble_visual(bubble: &WebviewWindow) {
    let _ = bubble.eval(
        "const el=document.querySelector('.ant-bubble');if(el){el.classList.remove('is-launching','is-dragging');delete el.dataset.dragSuppress;}",
    );
}
