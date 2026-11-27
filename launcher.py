# launcher.py
import os

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

import sys

VRY_APPDATA = os.path.join(os.getenv("APPDATA"), "vry")
os.makedirs(VRY_APPDATA, exist_ok=True)

if getattr(sys, "frozen", False):
    log_file = open(os.path.join(VRY_APPDATA, "output.log"), "w", encoding="utf-8")
    sys.stdout = log_file
    sys.stderr = log_file

import threading

import webview


def run_backend():
    try:
        import main

        main.run()
    except Exception:
        import traceback

        with open(os.path.join(VRY_APPDATA, "launcher_error.txt"), "w") as f:
            f.write(traceback.format_exc())


if __name__ == "__main__":
    threading.Thread(target=run_backend, daemon=True).start()

    base = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base, "vry-gui.html")

    webview.create_window(
        title="VALORANT rank yoinker",
        url=f"file:///{html_path}",
        width=1280,
        height=800,
        resizable=True,
        min_size=(900, 600),
        background_color="#0a0b0f",
    )

    # private_mode=False so localStorage (rank icon cache) survives restarts
    webview.start(gui="edgechromium", private_mode=False, storage_path=os.path.join(VRY_APPDATA, "webview"))
    os._exit(0)
