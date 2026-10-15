from pathlib import Path
import os
import sys
from importlib.metadata import distribution

# Prevent unrelated tools on PATH from supplying incompatible Windows DLLs.
if sys.platform == "win32":
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    os.environ["PATH"] = os.pathsep.join(
        str(path) for path in (Path(sys.executable).parent, Path(sys.base_prefix), windows / "System32", windows)
    )

project_root = Path(SPECPATH).parent
notices = []
for package in ("numpy", "soundfile", "sounddevice", "PySide6", "PySide6_Essentials", "shiboken6", "cffi"):
    dist = distribution(package)
    for entry in dist.files or []:
        if any(term in str(entry).lower() for term in ("license", "copying", "copyright")):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                notices.append((str(source), str(Path("third-party-licenses") / package / Path(str(entry)).parent)))
analysis = Analysis(
    [str(project_root / "src/e2sample_manager/app.py")],
    pathex=[str(project_root / "src")],
    datas=[(str(project_root / "assets/e2sample-manager.svg"), "assets"),
           (str(project_root / "README.md"), "."),
           (str(project_root / "LICENSE"), "."),
           (str(project_root / "ATTRIBUTION.md"), ".")] + notices,
    hiddenimports=["sounddevice", "soundfile"],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml"],
    binaries=[], hookspath=[], hooksconfig={}, runtime_hooks=[], noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="ElectribeStudio",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=os.environ.get("E2_BUILD_CONSOLE") == "1")
COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="ElectribeStudio")
