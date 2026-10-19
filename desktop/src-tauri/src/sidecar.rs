//! Starts, supervises and stops the Python FastAPI sidecar.
//!
//! - Port: a free loopback port chosen at launch.
//! - Auth: a random per-session token passed through the environment (never
//!   argv, which other processes can read) and returned to the UI over IPC.
//! - Lifetime: on Windows the sidecar is placed in a Job Object with
//!   KILL_ON_JOB_CLOSE, so it (and anything it spawns) dies with the app even on
//!   a crash. The sidecar also watches `--parent-pid` as a second safety net.
//! - Recovery: `restart` replaces a crashed sidecar with a fresh one.

use std::net::TcpListener;
#[cfg(debug_assertions)]
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

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

struct Running {
    connection: BackendConnection,
    child: Child,
    #[cfg(windows)]
    _job: Option<job::KillOnClose>,
}

pub struct Sidecar {
    app: AppHandle,
    token: String,
    state: Mutex<Option<Running>>,
}

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
    pub fn start(app: &AppHandle) -> Result<Self, BoxError> {
        let token = format!("{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple());
        let sidecar = Self { app: app.clone(), token, state: Mutex::new(None) };
        let running = sidecar.spawn()?;
        *sidecar.state.lock().map_err(|_| "sidecar state poisoned")? = Some(running);
        Ok(sidecar)
    }

    fn spawn(&self) -> Result<Running, BoxError> {
        let port = TcpListener::bind("127.0.0.1:0")?.local_addr()?.port();
        let mut command = backend_command(&self.app)?;
        command
            .args(["--port", &port.to_string(), "--parent-pid", &std::process::id().to_string()])
            .env("RESUMEFLOW_SESSION_TOKEN", &self.token)
            .env("RESUMEFLOW_ALLOWED_ORIGINS", serde_json::to_string(ALLOWED_ORIGINS)?)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }

        let child = command.spawn()?;
        Ok(Running {
            connection: BackendConnection { base_url: format!("http://127.0.0.1:{port}"), token: self.token.clone() },
            #[cfg(windows)]
            _job: job::KillOnClose::assign(&child),
            child,
        })
    }

    pub fn connection(&self) -> Option<BackendConnection> {
        self.state.lock().ok()?.as_ref().map(|r| r.connection.clone())
    }

    pub fn status(&self) -> BackendStatus {
        let Ok(mut guard) = self.state.lock() else {
            return BackendStatus { running: false, exit_code: None };
        };
        match guard.as_mut().map(|r| r.child.try_wait()) {
            Some(Ok(None)) => BackendStatus { running: true, exit_code: None },
            Some(Ok(Some(status))) => BackendStatus { running: false, exit_code: status.code() },
            _ => BackendStatus { running: false, exit_code: None },
        }
    }

    /// Stops the current sidecar (if any) and starts a new one on a fresh port.
    pub fn restart(&self) -> Result<BackendConnection, BoxError> {
        self.stop();
        let running = self.spawn()?;
        let connection = running.connection.clone();
        *self.state.lock().map_err(|_| "sidecar state poisoned")? = Some(running);
        Ok(connection)
    }

    pub fn stop(&self) {
        if let Ok(mut guard) = self.state.lock() {
            if let Some(mut running) = guard.take() {
                let _ = running.child.kill();
                let _ = running.child.wait();
            }
        }
    }
}

/// Development: run the backend from the repo's virtualenv with a repo-local data dir.
#[cfg(debug_assertions)]
fn backend_command(_app: &AppHandle) -> Result<Command, BoxError> {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let backend_dir = repo.join("backend");
    let python = std::env::var_os("RESUMEFLOW_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| backend_dir.join(".venv").join("Scripts").join("python.exe"));

    let mut command = Command::new(python);
    command
        .args(["-m", "resumeflow"])
        .current_dir(&backend_dir)
        .env("RESUMEFLOW_DATA_DIR", repo.join(".dev-data"));
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

#[cfg(windows)]
mod job {
    use std::os::windows::io::AsRawHandle;

    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    /// Closing this handle (on drop, or when the app process dies) kills every process in the job.
    pub struct KillOnClose(HANDLE);

    // The handle is only used to keep the job alive and to close it.
    unsafe impl Send for KillOnClose {}
    unsafe impl Sync for KillOnClose {}

    impl KillOnClose {
        pub fn assign(child: &std::process::Child) -> Option<Self> {
            unsafe {
                let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if job.is_null() {
                    return None;
                }
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    job,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const core::ffi::c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 || AssignProcessToJobObject(job, child.as_raw_handle() as HANDLE) == 0 {
                    CloseHandle(job);
                    return None;
                }
                Some(Self(job))
            }
        }
    }

    impl Drop for KillOnClose {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}
