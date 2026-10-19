"""DOCX -> PDF conversion (PRD §2, §9): LibreOffice headless, or Microsoft Word via COM.

Failures never lose work: the DOCX is already written, and conversion can be retried.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from resumeflow.services.settings import AppSettings

logger = logging.getLogger(__name__)
_word_lock = threading.Lock()

LIBREOFFICE_LOCATIONS = [
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
]


class PdfConversionError(Exception):
    pass


def find_libreoffice(configured: str | None) -> Path | None:
    candidates = [Path(configured)] if configured else []
    which = shutil.which("soffice")
    if which:
        candidates.append(Path(which))
    candidates.extend(LIBREOFFICE_LOCATIONS)
    return next((path for path in candidates if path.is_file()), None)


def word_available() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Winword.exe"):
                return True
        except OSError:
            continue
    return False


def resolve_converter(settings: AppSettings) -> str | None:
    choice = settings.pdf_converter
    if choice == "none":
        return None
    libreoffice = find_libreoffice(settings.libreoffice_path)
    if choice == "libreoffice":
        return "libreoffice" if libreoffice else None
    if choice == "word":
        return "word" if word_available() else None
    return "libreoffice" if libreoffice else ("word" if word_available() else None)


def converter_status(settings: AppSettings) -> dict:
    libreoffice = find_libreoffice(settings.libreoffice_path)
    return {
        "configured": settings.pdf_converter,
        "libreoffice_path": str(libreoffice) if libreoffice else None,
        "word_available": word_available(),
        "active": resolve_converter(settings),
    }


def _convert_libreoffice(soffice: Path, sources: list[Path], workdir: Path) -> None:
    """One LibreOffice start for all documents."""
    profile = (workdir / "lo-profile").resolve().as_uri()
    result = subprocess.run(
        [
            str(soffice),
            "--headless",
            "--norestore",
            "--nolockcheck",
            f"-env:UserInstallation={profile}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(workdir),
            *(str(s) for s in sources),
        ],
        capture_output=True,
        timeout=180 + 60 * len(sources),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    missing = [s.name for s in sources if not (workdir / f"{s.stem}.pdf").exists()]
    if result.returncode != 0 or missing:
        detail = (result.stderr or result.stdout or b"").decode(errors="replace").strip()[:300]
        raise PdfConversionError(f"LibreOffice could not convert {', '.join(missing) or 'the documents'}. {detail}".strip())


def _word_export(source: Path, output: Path) -> None:
    """One document per Word session. Reusing a session for a second document proved unreliable over COM
    ("<unknown>.Open"), and a failed retry costs more than the second start it would save."""
    import win32com.client

    word = win32com.client.DispatchEx("Word.Application")
    try:
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(str(source), ReadOnly=True, AddToRecentFiles=False, Visible=False)
        try:
            document.ExportAsFixedFormat(OutputFileName=str(output), ExportFormat=17)  # wdExportFormatPDF
        finally:
            document.Close(False)
    finally:
        try:
            word.Quit()
        except Exception:
            pass


def _convert_word(sources: list[Path], workdir: Path) -> None:
    import pythoncom

    with _word_lock:
        pythoncom.CoInitialize()
        try:
            for source in sources:
                output = workdir / f"{source.stem}.pdf"
                for attempt in (1, 2):
                    try:
                        _word_export(source, output)
                        break
                    except Exception as exc:
                        if attempt == 2:
                            raise PdfConversionError(f"Microsoft Word could not convert the document: {exc}") from exc
                        logger.warning("Word conversion failed (%s); retrying with a fresh Word instance", exc)
                        time.sleep(1.5)
        finally:
            pythoncom.CoUninitialize()
    missing = [s.name for s in sources if not (workdir / f"{s.stem}.pdf").exists()]
    if missing:
        raise PdfConversionError(f"Microsoft Word produced no PDF for {', '.join(missing)}")


def convert_documents_to_pdf(documents: list[bytes], settings: AppSettings) -> list[bytes]:
    """Converts DOCX documents (as bytes) to PDFs, in the same order. LibreOffice does them in one run."""
    converter = resolve_converter(settings)
    if converter is None:
        raise PdfConversionError(
            "No PDF converter is available. Install LibreOffice or Microsoft Word, or choose a converter in Settings."
        )
    with tempfile.TemporaryDirectory(prefix="resumeflow-pdf-") as tmp:
        workdir = Path(tmp)
        # Plain ASCII names in a short folder: converters dislike long or unusual paths.
        sources = []
        for index, data in enumerate(documents, start=1):
            source = workdir / f"document{index}.docx"
            source.write_bytes(data)
            sources.append(source)
        if converter == "libreoffice":
            _convert_libreoffice(find_libreoffice(settings.libreoffice_path), sources, workdir)  # type: ignore[arg-type]
        else:
            _convert_word(sources, workdir)
        return [(workdir / f"{s.stem}.pdf").read_bytes() for s in sources]


def convert_to_pdf_bytes(docx: Path, settings: AppSettings) -> bytes:
    """Converts one DOCX file and returns the PDF bytes; the caller decides where to write them."""
    return convert_documents_to_pdf([docx.read_bytes()], settings)[0]
