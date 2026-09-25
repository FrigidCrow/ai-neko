# PyInstaller 6 onedir build. The driver sets clean, temporary work/dist paths.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

repo = Path(SPECPATH).parent
# LangChain Core exposes many symbols through __getattr__/import_module. Collect
# its own modules, not optional provider SDKs or other frameworks.
hidden = collect_submodules("langchain_core") + [
    "langgraph.checkpoint.sqlite",
    "langsmith.run_helpers",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.websockets_sansio_impl",
    "uvicorn.lifespan.on",
]
metadata = []
for name in ("langgraph", "langgraph-checkpoint-sqlite", "fastapi", "uvicorn", "websockets", "filelock"):
    metadata += copy_metadata(name, recursive=True)

a = Analysis(
    [str(repo / "packaging" / "entry.py")],
    pathex=[str(repo / "src")],
    binaries=[],
    datas=metadata,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # These installed build/test tools must never enter the runtime bundle via
    # optional LangSmith test integrations or PyInstaller's import analysis.
    excludes=[
        "pytest", "_pytest", "ruff", "PyInstaller", "tkinter", "langsmith.testing",
        "pygments", "setuptools", "pkg_resources", "_distutils_hack",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="ai-neko",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ai-neko")
