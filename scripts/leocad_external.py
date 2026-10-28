#!/usr/bin/env python3
"""Run a shared LeoCAD binary with a private headless display."""

import os
from pathlib import Path
import select
import subprocess
import sys


def main() -> int:
    renderer = Path(os.environ["INSTRUCTIONS_LEOCAD_BIN"]).expanduser()
    xvfb = Path(os.environ["INSTRUCTIONS_XVFB_BIN"]).expanduser()
    if not renderer.is_file() or not xvfb.is_file():
        raise FileNotFoundError("说明书的 LeoCAD 或 Xvfb 路径不存在")
    runtime = Path(__file__).resolve().parents[1] / "runs/lego-instructions/.runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.chmod(0o700)
    read_fd, write_fd = os.pipe()
    display = subprocess.Popen(
        [str(xvfb), "-displayfd", str(write_fd), "-screen", "0", "1600x1200x24",
         "-nolisten", "tcp", "-ac"],
        pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.close(write_fd)
    try:
        if not select.select([read_fd], [], [], 15)[0]:
            raise RuntimeError("Xvfb 启动超时")
        number = os.read(read_fd, 64).decode().strip()
        if not number.isdigit():
            raise RuntimeError("Xvfb 未能启动显示")
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
