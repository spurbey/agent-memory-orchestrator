# Antelligent Tauri Shell Guide

This folder contains the Rust/Tauri shell for Antelligent. The shell owns native
desktop behavior only: windows, tray, PID, launch config, and daemon supervision.

It must not own AMO memory, retrieval, peer-agent, provider API, or peer-netd
logic.

## File Map

```text
src-tauri/
  tauri.conf.json          Tauri app, window, build, and bundle config.
  Cargo.toml               Rust dependencies.
  build.rs                 Tauri build integration.
  capabilities/default.json
                           Allowed Tauri capabilities.
  icons/                   App icon assets required by Tauri builds.
  src/
    main.rs                Binary entry point.
    lib.rs                 Tauri builder, plugins, commands, setup.
    config.rs              Launch config, token path, backend info.
    supervisor.rs          Daemon reachability check and spawn attempt.
    window.rs              Bubble/panel positioning, show/hide, animation hooks.
    tray.rs                Tray menu and tray click behavior.
    pid.rs                 PID file write/remove.
```

Generated folders such as `target/` and `gen/` are build output or generated
schema material. Do not put hand-written product logic there.

## Native Window Model

`tauri.conf.json` declares two windows:

```text
bubble
  url: index.html#bubble
  transparent, undecorated, always-on-top, skip taskbar
  small floating ant icon

panel
  url: index.html#panel
  transparent, undecorated, always-on-top, resizable
  hidden until opened from bubble or tray
```

The frontend decides which UI to mount by reading the URL hash in
`apps/antelligent/src/main.ts`.

## Rust Command Surface

`src/lib.rs` currently exposes three commands to TypeScript:

```text
backend_info() -> { baseUrl, token }
show_panel()
hide_panel()
```

Keep commands small. If a command needs AMO state, add a daemon endpoint instead
of reading files or running AMO logic from Rust.

## Startup Flow

```text
main.rs
  -> antelligent_lib::run()
  -> tauri::Builder
  -> single-instance plugin
  -> setup:
       pid::write_pid()
       supervisor::ensure_daemon_started()
       tray::install()
       window::place_bubble()
```

Single-instance behavior:

- First launch creates bubble and tray.
- Second launch focuses/shows the existing panel.
- No duplicate bubbles.
- No duplicate daemon spawn loop.

## Launch Config And Token

`config.rs` resolves local backend information.

Launch config path precedence:

```text
1. ANTELLIGENT_CONFIG
2. AMO_HOME/.ui/antelligent.launch.json
3. ~/.agent-memory-orchestrator/.ui/antelligent.launch.json
```

Development-only fallbacks:

```text
ANTELLIGENT_DAEMON_URL
AMO_DAEMON_URL
```

Installed launch config is written by:

```text
src/agent_memory_orchestrator/runtime/antelligent/launch_config.py
```

Expected launch config shape:

```json
{
  "schema_version": 1,
  "amo_home": "...",
  "daemon_url": "http://127.0.0.1:8765",
  "daemon_command": {
    "program": "...absolute path to AMO runtime Python...",
    "args": [
      "-m",
      "agent_memory_orchestrator.runtime.daemon.server",
      "--amo-home",
      "..."
    ]
  },
  "ui_token_path": ".../.ui/antelligent.token"
}
```

The app reads the token from `ui_token_path` and returns it to TypeScript through
`backend_info`. The token is only for localhost daemon UI access. Never place it
in registry values, LaunchAgent plist files, logs, or release artifacts.

## Daemon Supervision

`supervisor.rs` does a small reachability check against the configured daemon
URL. If unreachable and `daemon_command` exists, it spawns that command.

Rules:

- Use configured command only.
- Do not rely on `PATH`.
- Apply spawn cooldown to avoid tight loops.
- Hide the console window on Windows.
- Do not stop the daemon when Antelligent exits.
- Let daemon owner-lock reject duplicate daemon instances.

If daemon spawning fails, Antelligent should show offline/error through the UI
status endpoint once the daemon is reachable again.

## Tray And Exit Behavior

`tray.rs` owns tray menu and tray click behavior.

Current behavior:

- Show Antelligent: show/focus panel.
- Hide Panel: hide panel and restore bubble.
- Exit: clear Antelligent PID and exit the desktop shell only.
- Left-click tray icon: show/focus panel.

Exit must not stop:

- `amo-daemon`
- `amo-peer-netd`
- `peer-agent watch`

Those are AMO-managed runtime services.

## PID Handling

`pid.rs` writes a small PID JSON file on startup and removes it on clean exit.

Path comes from:

```text
config::pid_path()
```

The Python process manager in:

```text
src/agent_memory_orchestrator/runtime/antelligent/process.py
```

uses that PID for `amo-cli antelligent status` and `amo-cli antelligent stop`.
The Python side must validate process identity before killing anything. Rust
should only write/remove its own PID file.

## Window Behavior

`window.rs` owns:

- top-right initial bubble placement,
- panel sizing and placement from bubble position,
- showing panel from bubble/tray,
- hiding panel back to bubble,
- injecting small CSS class changes for open/close animation reset.

The visual animation itself belongs to CSS in `apps/antelligent/src/styles`.

When changing window behavior:

1. Keep native placement and visibility in Rust.
2. Keep visual design and animation timing in CSS/TypeScript.
3. Test both bubble-to-panel and panel-to-bubble transitions.
4. Test second launch with the single-instance plugin.

## Release Build Notes

`tauri.conf.json` has:

```json
"bundle": {
  "active": false,
  "targets": []
}
```

Internal v1 uses portable artifacts rather than MSI/DMG installers. GitHub
Actions builds app outputs and packages them into zip/tar.gz artifacts.

Related files:

```text
.github/workflows/antelligent-release.yml
scripts/package_antelligent_artifact.py
src/agent_memory_orchestrator/runtime/antelligent/artifacts.py
src/agent_memory_orchestrator/runtime/antelligent/install.py
```

Do not enable MSI/DMG as the default release path until signing and notarization
are ready.

## Adding Native Features

Add a new Tauri command:

1. Implement a small function in the relevant Rust module.
2. Add a `#[tauri::command]` wrapper in `lib.rs` if needed.
3. Add it to `tauri::generate_handler!`.
4. Call it from TypeScript with `invoke`.
5. Keep sensitive state out of command arguments.

Add a new tray item:

1. Add `MenuItem` in `tray.rs`.
2. Handle it in `on_menu_event`.
3. Keep long-running work out of the tray handler.

Add a new window:

1. Add the window in `tauri.conf.json`.
2. Add placement/show/hide behavior in `window.rs`.
3. Add a URL hash route in `apps/antelligent/src/main.ts`.
4. Add focused CSS for that window mode.

## Guardrails

- Do not call provider APIs from Rust.
- Do not read or mutate peer room files from Rust.
- Do not implement peer-netd transport logic here.
- Do not store API keys or peer secrets in Tauri config.
- Do not kill AMO services from Antelligent exit.
- Do not create startup entries from Rust; use the Python installer/startup code.
