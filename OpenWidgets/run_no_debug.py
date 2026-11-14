import os
import sys
import subprocess

# hard-disable webview remote debugging
os.environ["WEBVIEW_REMOTE_DEBUG"] = "0"

# remove any existing qt/chromium debug flags
os.environ.pop("QTWEBENGINE_REMOTE_DEBUGGING", None)

flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
flags = " ".join(
    f for f in flags.split()
    if not f.startswith("--remote-debugging-port")
)

if flags:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags
else:
    os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)

# launch main.py using the same python executable
subprocess.run([sys.executable, "main.py"], check=True)
