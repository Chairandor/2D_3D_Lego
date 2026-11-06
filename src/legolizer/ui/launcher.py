"""从本项目启动队友同款的 2D/3D 合并工作台。"""

from pathlib import Path
import os
import runpy
import sys


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[3]
    interface = root / "pipeline_ui.py"
    if not interface.is_file():
        interface = root / "2D_3D_Lego/pipeline_ui.py"
    if not interface.is_file():
        raise SystemExit("找不到 pipeline_ui.py，请在包含合并工作台源码的项目中运行")
    args = list(sys.argv[1:] if argv is None else argv)
    if "--root" in args:
        position = args.index("--root")
        del args[position:position + 2]
    args = ["--no-browser" if value == "--no-open" else value for value in args]
    os.environ.setdefault("LEGOLIZER_ROOT", str(root))
    os.environ.setdefault("PIPELINE_UI_DEFAULT_MODE", "3d")
    os.environ.setdefault("PROCESSING3D_PYTHON", sys.executable)
    sys.path.insert(0, str(interface.parent))
    previous = sys.argv
    try:
        sys.argv = [str(interface), *args]
        runpy.run_path(str(interface), run_name="__main__")
    finally:
        sys.argv = previous
    return 0
