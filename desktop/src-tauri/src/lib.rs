mod sidecar;

use sidecar::{BackendConnection, BackendStatus, Sidecar};
use tauri::{Manager, State};

/// Hands the UI the shared backend's loopback URL and saved token.
#[tauri::command]
fn backend_connection(sidecar: State<'_, Sidecar>) -> Result<BackendConnection, String> {
    sidecar.connection()
}

/// Lets the UI distinguish "still starting" from "process exited".
#[tauri::command]
fn backend_status(sidecar: State<'_, Sidecar>) -> BackendStatus {
    sidecar.status()
}

/// Starts the shared backend again if it stopped answering. Async so the wait never blocks the window.
#[tauri::command]
async fn restart_backend(sidecar: State<'_, Sidecar>) -> Result<BackendConnection, String> {
    sidecar.restart().map_err(|e| e.to_string())
}

pub fn run() {
    // The backend is shared with the Chrome extension, so it keeps running when this window closes.
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let sidecar = Sidecar::start(app.handle())?;
            app.manage(sidecar);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_connection, backend_status, restart_backend])
        .run(tauri::generate_context!())
        .expect("failed to run ResumeFlow AI");
}
