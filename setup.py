# setup.py
import sys
from cx_Freeze import setup, Executable
from src.constants import version

build_exe_options = {
    "path": sys.path,
    "include_files": [
        'configurator.bat',
        'updatescript.bat',
        'vry-gui.html',          # ← include the HTML frontend
    ],
    "packages": [
        "requests", "colr", "InquirerPy", "websockets",
        "pypresence", "nest_asyncio", "rich", "websocket_server",
        "webview",               # ← add pywebview
        "clr",                   # webview dependency on Windows
    ],
    "excludes": ["tkinter", "test", "unittest", "pygments", "xmlrpc"],
}

setup(
    name="VALORANT rank yoinker",
    version=version,
    description='vRY - VALORANT rank yoinker',
    executables=[
        Executable(
            "launcher.py",          # ← new entry point
            icon="./assets/Logo.ico",
            target_name="vRY.exe",
            base="Win32GUI",        # ← THIS hides the terminal window
        )
    ],
    options={"build_exe": build_exe_options},
)