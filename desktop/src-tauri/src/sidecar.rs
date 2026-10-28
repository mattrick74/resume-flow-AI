//! Connects the desktop app to the shared ResumeFlow backend (v2.0).
//!
//! One local backend serves both front ends: this desktop app and the Chrome extension.
//! - Address: http://127.0.0.1:8765 in release builds. Debug builds use port 8766 and the repo's `.dev-data` folder,
//!   so development never touches real data.
//! - Auth: the backend's saved token in `<data folder>/api_token`, read from disk (same Windows user; the extension
//!   gets the same token by pairing).
//! - Lifetime: when no backend answers, the app starts one. It keeps running after the window closes, so the
//!   extension keeps working; `scripts/backend/install-startup.ps1` starts it at sign-in instead. A second start
//!   exits on its own (the backend allows one instance per port).

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::AppHandle;
#[cfg(not(debug_assertions))]
use tauri::Manager;

type BoxError = Box<dyn std::error::Error>;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendConnection {
    pub base_url: String,
    pub token: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BackendStatus {
    pub running: bool,
    pub exit_code: Option<i32>,
}

pub struct Sidecar {
    app: AppHandle,
    port: u16,
    data_dir: PathBuf,
    /// The backend this app started, if it did (only to report an early exit; it is never stopped from here).
    started: Mutex<Option<Child>>,
}

#[cfg(debug_assertions)]
const PORT: u16 = 8766;
#[cfg(not(debug_assertions))]
const PORT: u16 = 8765;

#[cfg(debug_assertions)]
const ALLOWED_ORIGINS: &[&str] = &[
    "http://127.0.0.1:1420",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
];
#[cfg(not(debug_assertions))]
const ALLOWED_ORIGINS: &[&str] = &["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"];

impl Sidecar {
    /// Uses the running backend, or starts one. Never waits: the UI shows "Starting…" until /health answers.
    pub fn start(app: &AppHandle) -> Result<Self, BoxError> {
        let sidecar = Self { app: app.clone(), port: PORT, data_dir: data_dir(), started: Mutex::new(None) };
        if !sidecar.healthy() {
            sidecar.spawn()?;
        }
        Ok(sidecar)
    }

    fn spawn(&self) -> Result<(), BoxError> {
        let mut command = backend_command(&self.app)?;
        command
            .args(["--port", &self.port.to_string()])
            // No per-session token: the backend uses its saved one, shared with the Chrome extension.
            .env_remove("RESUMEFLOW_SESSION_TOKEN")
            .env("RESUMEFLOW_DATA_DIR", &self.data_dir)
            .env("RESUMEFLOW_ALLOWED_ORIGINS", serde_json::to_string(ALLOWED_ORIGINS)?)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
            command.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
        }

        let child = command.spawn()?;
        *self.started.lock().map_err(|_| "backend state poisoned")? = Some(child);
        Ok(())
    }

    /// The backend's address and saved token. Fails while a freshly started backend hasn't written its token yet;
    /// the UI retries.
    pub fn connection(&self) -> Result<BackendConnection, String> {
        read_connection(&self.data_dir, self.port)
    }

    pub fn status(&self) -> BackendStatus {
        if self.healthy() {
            return BackendStatus { running: true, exit_code: None };
        }
        let exit_code = self
            .started
            .lock()
            .ok()
            .and_then(|mut guard| guard.as_mut().and_then(|child| child.try_wait().ok().flatten()))
            .and_then(|status| status.code());
        BackendStatus { running: false, exit_code }
    }

    /// Starts the backend again if it isn't answering, and waits (up to 60 s) until it does.
    pub fn restart(&self) -> Result<BackendConnection, BoxError> {
        if !self.healthy() {
            self.spawn()?;
        }
        let deadline = Instant::now() + Duration::from_secs(60);
        while Instant::now() < deadline {
            if self.healthy() {
                if let Ok(connection) = self.connection() {
                    return Ok(connection);
                }
            }
            std::thread::sleep(Duration::from_millis(500));
        }
        Err("The ResumeFlow engine didn't start within a minute. See the backend log in the ResumeFlowAI data folder.".into())
    }

    fn healthy(&self) -> bool {
        healthy_at(self.port)
    }
}

/// The address and saved token of the backend whose data folder is `data_dir`. Fails while a freshly started backend
/// hasn't written its token yet.
fn read_connection(data_dir: &std::path::Path, port: u16) -> Result<BackendConnection, String> {
    let token = std::fs::read_to_string(data_dir.join("api_token")).map(|t| t.trim().to_string()).unwrap_or_default();
    if token.len() < 32 {
        return Err("The ResumeFlow engine is starting".to_string());
    }
    Ok(BackendConnection { base_url: format!("http://127.0.0.1:{port}"), token })
}

/// A ResumeFlow backend answers /health on this loopback port.
fn healthy_at(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(800)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    if stream.write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n").is_err() {
        return false;
    }
    let mut response = Vec::new();
    let _ = stream.take(16 * 1024).read_to_end(&mut response);
    let text = String::from_utf8_lossy(&response);
    text.starts_with("HTTP/1.1 200") && text.contains("\"version\"")
}

/// The backend's data folder: where its database and saved token live.
#[cfg(debug_assertions)]
fn data_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join(".dev-data")
}

#[cfg(not(debug_assertions))]
fn data_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("RESUMEFLOW_DATA_DIR") {
        return PathBuf::from(dir);
    }
    let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).or_else(|| std::env::var_os("USERPROFILE").map(PathBuf::from));
    base.unwrap_or_default().join("ResumeFlowAI")
}

/// Development: run the backend from the repo's virtualenv.
#[cfg(debug_assertions)]
fn backend_command(_app: &AppHandle) -> Result<Command, BoxError> {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let backend_dir = repo.join("backend");
    let python = std::env::var_os("RESUMEFLOW_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| backend_dir.join(".venv").join("Scripts").join("python.exe"));

    let mut command = Command::new(python);
    command.args(["-m", "resumeflow"]).current_dir(&backend_dir);
    Ok(command)
}

/// Release: the PyInstaller build bundled as a resource folder (see tauri.release.conf.json).
#[cfg(not(debug_assertions))]
fn backend_command(app: &AppHandle) -> Result<Command, BoxError> {
    let exe = app.path().resource_dir()?.join("sidecar").join("resumeflow-backend.exe");
    if !exe.exists() {
        return Err(format!("bundled backend not found at {}", exe.display()).into());
    }
    Ok(Command::new(exe))
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;

    use super::{healthy_at, read_connection};

    fn serve_once(response: &'static str) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut request = [0u8; 1024];
                let _ = stream.read(&mut request);
                let _ = stream.write_all(response.as_bytes());
            }
        });
        port
    }

    #[test]
    fn a_resumeflow_backend_is_recognized_by_its_health_answer() {
        let ok = serve_once("HTTP/1.1 200 OK
content-type: application/json

{\"status\":\"ok\",\"version\":\"2.0.0\"}");
        assert!(healthy_at(ok));
        let other = serve_once("HTTP/1.1 200 OK

<html>something else</html>");
        assert!(!healthy_at(other));
        let closed = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port(); // nothing listens after drop
        assert!(!healthy_at(closed));
    }

    #[test]
    fn the_saved_token_is_read_from_the_data_folder() {
        let dir = std::env::temp_dir().join(format!("rf-sidecar-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        assert!(read_connection(&dir, 8765).is_err()); // not written yet: still starting
        std::fs::write(dir.join("api_token"), format!("{}
", "a".repeat(64))).unwrap();
        let connection = read_connection(&dir, 8765).unwrap();
        assert_eq!(connection.base_url, "http://127.0.0.1:8765");
        assert_eq!(connection.token, "a".repeat(64));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
