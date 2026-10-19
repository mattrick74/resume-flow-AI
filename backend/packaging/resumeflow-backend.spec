# PyInstaller spec for the production sidecar.
#
# One-folder mode on purpose: a one-file exe re-launches itself as a child
# process, so killing it from the desktop shell would orphan the real server.
#
# Build: .venv\Scripts\python -m PyInstaller --noconfirm packaging\resumeflow-backend.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

backend = Path(SPECPATH).parent

a = Analysis(
    [str(backend / "packaging" / "sidecar_entry.py")],
    pathex=[str(backend)],
    datas=[
        # Alembic loads env.py and revision files from disk at runtime.
        (str(backend / "resumeflow" / "migrations"), "resumeflow/migrations"),
        # keyring discovers its Windows Credential Manager backend via entry points.
        *copy_metadata("keyring"),
        # python-docx needs its default document templates to create new files.
        *collect_data_files("docx"),
    ],
    hiddenimports=[
        *collect_submodules("uvicorn"),
        *collect_submodules("resumeflow"),
        "alembic.context",
        "alembic.op",
        "sqlalchemy.dialects.sqlite",
        "keyring.backends.Windows",
        "multipart",
        "python_multipart",
        "pymupdf",
        # Microsoft Word PDF conversion (COM)
        "win32com.client",
        "pythoncom",
        "pywintypes",
    ],
    excludes=["tkinter", "pytest", "_pytest", "PyInstaller", "resumeflow.migrations.env"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="resumeflow-backend",
    # Console subsystem keeps stdout/stderr usable; the shell starts it with
    # CREATE_NO_WINDOW, so no console window appears.
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="resumeflow-backend", upx=False)
