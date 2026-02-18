# launcher.py
import os
os.environ["PYTHONIOENCODING"] = "utf-8"import threading
import time
import sys
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
