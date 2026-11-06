"""项目统一几何与 LEGO 尺寸常量。"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_LEGACY_BACKEND = _ROOT / "research/opensource/legolization"
DEFAULT_PROJECT_DIR = _LEGACY_BACKEND if _LEGACY_BACKEND.is_dir() else _ROOT / "legolization"

STUD_PITCH_MM = 8.0
PLATE_HEIGHT_MM = 3.2
BRICK_HEIGHT_MM = 9.6

LDU_PER_MM = 2.5
LDU_PER_STUD = 20.0
LDU_PER_PLATE = 8.0
LDU_PER_BRICK = 24.0
