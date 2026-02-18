@echo off
cd /d "%~dp0"
pythonw launcher.py
```

Using `pythonw` instead of `python` hides the console when running from source too.

---

### How it all fits together
```
vRY.exe  (Win32GUI, no terminal)
    │
    ├─► Thread: main.py logic runs normally in background
    │       └─► WebSocket server starts on port 1100
    │
    └─► pywebview window opens vry-gui.html
            └─► HTML connects to ws://localhost:1100 ✓