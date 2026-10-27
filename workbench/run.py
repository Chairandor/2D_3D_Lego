"""Cross-platform entry point; PowerShell wrapper selects the interpreter."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from lego_workbench.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
