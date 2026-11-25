from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LDrawColor:
    code: int
    name: str
    rgb: tuple[int, int, int]


# 常用、不透明、可购买的 LEGO/LDraw 实色子集。完整合法零件-颜色组合由
# legolization/BrickLink 后端继续校验；这里负责稳定、可复现的视觉量化。
COMMON_LDRAW_COLORS: tuple[LDrawColor, ...] = (
    LDrawColor(0, "Black", (27, 42, 52)),
    LDrawColor(1, "Blue", (30, 90, 168)),
    LDrawColor(2, "Green", (0, 133, 43)),
    LDrawColor(4, "Red", (201, 26, 9)),
    LDrawColor(5, "Dark Pink", (211, 53, 157)),
    LDrawColor(7, "Light Gray", (138, 146, 141)),
    LDrawColor(9, "Light Blue", (180, 210, 227)),
    LDrawColor(10, "Bright Green", (75, 159, 74)),
    LDrawColor(11, "Light Turquoise", (85, 165, 175)),
    LDrawColor(12, "Salmon", (242, 112, 94)),
    LDrawColor(13, "Pink", (252, 151, 172)),
    LDrawColor(14, "Yellow", (242, 205, 55)),
    LDrawColor(15, "White", (244, 244, 244)),
    LDrawColor(19, "Tan", (228, 205, 158)),
    LDrawColor(22, "Purple", (129, 0, 123)),
    LDrawColor(25, "Orange", (254, 138, 24)),
    LDrawColor(26, "Magenta", (146, 57, 120)),
    LDrawColor(27, "Lime", (187, 233, 11)),
    LDrawColor(28, "Dark Tan", (149, 138, 115)),
    LDrawColor(70, "Reddish Brown", (88, 42, 18)),
    LDrawColor(71, "Light Bluish Gray", (160, 165, 169)),
    LDrawColor(72, "Dark Bluish Gray", (108, 110, 104)),
    LDrawColor(73, "Medium Blue", (90, 147, 219)),
    LDrawColor(74, "Medium Green", (115, 220, 161)),
    LDrawColor(77, "Light Pink", (254, 204, 207)),
    LDrawColor(78, "Light Nougat", (246, 215, 179)),
    LDrawColor(84, "Medium Nougat", (204, 112, 42)),
    LDrawColor(272, "Dark Blue", (10, 52, 99)),
    LDrawColor(288, "Dark Green", (24, 70, 50)),
    LDrawColor(320, "Dark Red", (114, 14, 15)),
    LDrawColor(321, "Dark Azure", (7, 139, 201)),
    LDrawColor(322, "Medium Azure", (54, 174, 191)),
    LDrawColor(323, "Light Aqua", (173, 195, 192)),
    LDrawColor(330, "Olive Green", (155, 154, 90)),
)

TIKO_LDRAW_COLORS = (
    LDrawColor(0, "Black", (27, 42, 52)),
    LDrawColor(6, "Brown", (84, 51, 36)),
    LDrawColor(19, "Tan", (215, 186, 140)),
    LDrawColor(22, "Purple", (103, 31, 129)),
    LDrawColor(28, "Dark Tan", (137, 125, 98)),
    LDrawColor(30, "Medium Lavender", (160, 110, 185)),
    LDrawColor(71, "Light Bluish Gray", (150, 150, 150)),
    LDrawColor(72, "Dark Bluish Gray", (100, 100, 100)),
    LDrawColor(308, "Dark Brown", (53, 33, 0)),
    LDrawColor(373, "Sand Purple", (117, 101, 125)),
)
_COMMON_CODES = {color.code for color in COMMON_LDRAW_COLORS}
TEXTURE_LDRAW_COLORS = COMMON_LDRAW_COLORS + tuple(color for color in TIKO_LDRAW_COLORS if color.code not in _COMMON_CODES)

_CODES = np.asarray([item.code for item in COMMON_LDRAW_COLORS], dtype=np.int16)
_RGBS = np.asarray([item.rgb for item in COMMON_LDRAW_COLORS], dtype=np.float64)
_SRGB_TO_LMS = np.asarray(
    (
        (0.4122214708, 0.5363325363, 0.0514459929),
        (0.2119034982, 0.6806995451, 0.1073969566),
        (0.0883024619, 0.2817188376, 0.6299787005),
    )
)
_LMS_TO_OKLAB = np.asarray(
    (
        (0.2104542553, 0.7936177850, -0.0040720468),
        (1.9779984951, -2.4285922050, 0.4505937099),
        (0.0259040371, 0.7827717662, -0.8086757660),
    )
)


def srgb_to_linear(values: np.ndarray) -> np.ndarray:
    """Decode normalized sRGB channels into linear light."""
    return np.where(values <= .04045, values / 12.92, ((values + .055) / 1.055) ** 2.4)


def linear_to_srgb(values: np.ndarray) -> np.ndarray:
    """Encode linear light channels for display in sRGB."""
    return np.where(values <= .0031308, 12.92 * values, 1.055 * np.maximum(values, 0.) ** (1 / 2.4) - .055)


def _srgb_to_oklab(values: np.ndarray) -> np.ndarray:
    scaled = np.asarray(values, dtype=np.float64).reshape(-1, 3) / 255.0
    linear = srgb_to_linear(scaled)
    return np.cbrt(linear @ _SRGB_TO_LMS.T) @ _LMS_TO_OKLAB.T


_PALETTE_OKLAB = _srgb_to_oklab(_RGBS)


def quantize_rgb_to_ldraw(rgb: np.ndarray, *, palette: tuple[LDrawColor, ...] = COMMON_LDRAW_COLORS) -> np.ndarray:
    """把任意 ``(..., 3)`` 的 0..1 或 0..255 RGB 映射为 LDraw 色号。"""
    values = np.asarray(rgb, dtype=np.float64)
    if values.shape[-1] != 3:
        raise ValueError("RGB 数组最后一维必须为 3")
    flat = values.reshape(-1, 3)
    if flat.size and float(np.nanmax(flat)) <= 1.0:
        flat = flat * 255.0
    pixels = _srgb_to_oklab(np.clip(flat, 0.0, 255.0))
    codes = _CODES if palette is COMMON_LDRAW_COLORS else np.asarray([color.code for color in palette], dtype=np.int16)
    palette_lab = _PALETTE_OKLAB if palette is COMMON_LDRAW_COLORS else _srgb_to_oklab([color.rgb for color in palette])
    delta = pixels[:, None, :] - palette_lab[None, :, :]
    # 提高色度权重，避免近黑/近灰错误吸附到深棕等饱和色。
    distance = delta[:, :, 0] ** 2 + 3.0 * (
        delta[:, :, 1] ** 2 + delta[:, :, 2] ** 2
    )
    return codes[np.argmin(distance, axis=1)].reshape(values.shape[:-1])


def color_name(code: int) -> str:
    return next((item.name for item in COMMON_LDRAW_COLORS if item.code == code), str(code))
