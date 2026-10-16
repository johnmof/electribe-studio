"""Persistent diagnostics for windowed Windows builds without a console."""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_fault_stream = None


def install() -> Path | None:
    global _fault_stream
    folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ElectribeStudio" / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            folder / "application.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger = logging.getLogger("electribe")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        fault_path = folder / "native-crash.log"
        if fault_path.exists() and fault_path.stat().st_size > 2_000_000:
            fault_path.replace(folder / "native-crash.previous.log")
        _fault_stream = fault_path.open("a", encoding="utf-8")
        faulthandler.enable(file=_fault_stream, all_threads=True)
    except OSError:
        return None

    def exception_hook(kind, value, traceback):
        logger.error("Unhandled Python exception", exc_info=(kind, value, traceback))
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is not None:
            QMessageBox.critical(
                None, "Programmfehler", f"{kind.__name__}: {value}\n\nFehlerprotokoll: {folder}"
            )

    sys.excepthook = exception_hook
    logger.info("Electribe Studio started; Python %s", sys.version.split()[0])
    return folder
