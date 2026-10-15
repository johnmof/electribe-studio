# PyInstaller configuration for the Linux one-folder distribution.
from pathlib import Path

from PyInstaller.depend.bindepend import resolve_library_path


project_root = Path(SPECPATH).parent
source_root = project_root / "src"
assets_root = project_root / "assets"

datas = [
    (str(assets_root / "e2sample-manager.svg"), "assets"),
    (str(assets_root / "e2sample-manager.desktop"), "assets"),
]
sndfile_library = resolve_library_path("libsndfile.so.1")
binaries = [(sndfile_library, ".")] if sndfile_library else []

analysis = Analysis(
    [str(source_root / "e2sample_manager" / "app.py")],
    pathex=[str(source_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["sounddevice", "soundfile"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="e2sample-manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="e2sample-manager",
)
