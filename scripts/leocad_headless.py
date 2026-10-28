"""Isolated Xvfb display for the native LeoCAD instruction renderer.

The extracted binaries are optional local resources, never bundled in Git.
No system service, fixed display number, or GPU display server is required.
"""
from __future__ import annotations

import os
from pathlib import Path
import select
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    xvfb = root / ".runtime/xvfb/usr/bin/Xvfb"
    renderer = root / ".runtime/leocad/squashfs-root/AppRun"
    if not xvfb.is_file() or not renderer.is_file():
        raise RuntimeError("Configure extracted LeoCAD and Xvfb first; see docs/SETUP_LINUX.md")
    runtime = root / ".runtime/tmp"
    runtime.mkdir(parents=True, exist_ok=True)
    read_fd, write_fd = os.pipe()
    display = subprocess.Popen([str(xvfb), "-displayfd", str(write_fd), "-screen", "0",
                                "1600x1200x24", "-nolisten", "tcp", "-ac"],
                               pass_fds=(write_fd,), stdout=subprocess.DEVNULL)
    os.close(write_fd)
    try:
        if not select.select([read_fd], [], [], 15)[0]:
            raise RuntimeError("Xvfb startup timed out")
        number = os.read(read_fd, 64).decode().strip()
        if not number.isdigit():
            raise RuntimeError("Xvfb failed to start a display")
        env = os.environ.copy()
        env.update(DISPLAY=":" + number, QT_QPA_PLATFORM="xcb", LIBGL_ALWAYS_SOFTWARE="1",
                   __GLX_VENDOR_LIBRARY_NAME="mesa", XDG_RUNTIME_DIR=str(runtime))
        return subprocess.run([str(renderer), *sys.argv[1:]], env=env, check=False).returncode
    finally:
        os.close(read_fd)
        display.terminate()
        try:
            display.wait(timeout=5)
        except subprocess.TimeoutExpired:
            display.kill()
            display.wait()


if __name__ == "__main__":
    raise SystemExit(main())
