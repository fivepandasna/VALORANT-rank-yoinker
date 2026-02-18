# launcher.py
import os
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

import sys
import io

if getattr(sys, 'frozen', False):
    # Redirect all output to a log file in frozen exe
    log_file = open(os.path.join(os.path.dirname(sys.executable), 'output.log'), 'w', encoding='utf-8')
    sys.stdout = log_file
    sys.stderr = log_file
    
import threading
import time
import webview

def run_backend():
    try:
        import main
    except SystemExit:
        pass
    except Exception:
        import traceback
        with open(os.path.join(os.getcwd(), "launcher_error.txt"), "w") as f:
            f.write(traceback.format_exc())

def wait_for_backend(timeout=30):
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 1100), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False

if __name__ == "__main__":
    backend_thread = threading.Thread(target=run_backend, daemon=True)
    backend_thread.start()

    wait_for_backend(timeout=30)

    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    html_path = os.path.join(base, "vry-gui.html")

    window = webview.create_window(
        title="VALORANT rank yoinker",
        url=f"file:///{html_path}",
        width=1280,
        height=800,
        resizable=True,
        min_size=(900, 600),
        background_color='#0a0b0f',
    )

    webview.start(gui='edgechromium')
    os._exit(0)
