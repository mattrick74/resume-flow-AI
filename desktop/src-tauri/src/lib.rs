mod sidecar;

use sidecar::{BackendConnection, BackendStatus, Sidecar};
use tauri::{Manager, RunEvent, State};

/// Hands the UI the sidecar's loopback URL and per-session token.
#[tauri::command]
fn backend_connection(sidecar: State<'_, Sidecar>) -> Result<BackendConnection, String> {
    sidecar.connection().ok_or_else(|| "The ResumeFlow engine isn't running".to_string())
}

/// Lets the UI distinguish "still starting" from "process exited".
#[tauri::command]
fn backend_status(sidecar: State<'_, Sidecar>) -> BackendStatus {
    sidecar.status()
}

/// Replaces a crashed or unresponsive sidecar with a fresh one.
#[tauri::command]
fn restart_backend(sidecar: State<'_, Sidecar>) -> Result<BackendConnection, String> {
    sidecar.restart().map_err(|e| e.to_string())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let sidecar = Sidecar::start(app.handle())?;
            app.manage(sidecar);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_connection, backend_status, restart_backend])
        .build(tauri::generate_context!())
        .expect("failed to build ResumeFlow AI");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(sidecar) = handle.try_state::<Sidecar>() {
                sidecar.stop();
            }
        }
    });
}
