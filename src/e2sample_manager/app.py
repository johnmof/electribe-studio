"""Application entry point."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QLocale, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from e2sample_manager.ui import MainWindow
from e2sample_manager.ui.theme import apply_theme


def _asset_path(name: str) -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return Path(bundle_root) / "assets" / name
    return Path(__file__).resolve().parents[2] / "assets" / name


def main(argv: Sequence[str] | None = None) -> int:
    """Run the GUI, optionally opening the first positional file path."""

    arguments = list(sys.argv if argv is None else argv)
    from e2sample_manager.diagnostics import install

    if "--ui-self-test" in arguments:
        from e2sample_manager.selftest import run_ui

        report = run_ui()
        destination = next(
            (a.split("=", 1)[1] for a in arguments if a.startswith("--self-test-report=")), None
        )
        if destination:
            Path(destination).write_text(report, encoding="utf-8")
        if sys.stdout is not None:
            print(report)
        return 0
    if "--self-test" in arguments:
        from e2sample_manager.selftest import run

        try:
            report, result = run(), 0
        except Exception as exc:
            report, result = f"FAIL: {type(exc).__name__}: {exc}", 1
        destination = next(
            (a.split("=", 1)[1] for a in arguments if a.startswith("--self-test-report=")), None
        )
        if destination:
            Path(destination).write_text(report, encoding="utf-8")
        if sys.stdout is not None:
            print(report)
        return result
    install()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(arguments)
    apply_theme(app)
    app.setApplicationName("e2sample-manager")
    app.setApplicationDisplayName("e2sample-manager")
    app.setOrganizationName("e2sample-manager")
    app.setWindowIcon(QIcon(str(_asset_path("e2sample-manager.svg"))))
    QLocale.setDefault(QLocale(QLocale.German, QLocale.Germany))
    path = next((argument for argument in arguments[1:] if not argument.startswith("-")), None)
    window = MainWindow(path)
    window.show()
    smoke_timeout = os.environ.get("E2SAMPLE_MANAGER_SMOKE_TIMEOUT_MS")
    if smoke_timeout is not None:
        try:
            timeout_ms = max(0, int(smoke_timeout))
        except ValueError:
            timeout_ms = 1000
        QTimer.singleShot(timeout_ms, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
