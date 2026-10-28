"""Environment checks and a session-only LDConfig colour fallback."""
import importlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys
import types

def ensure_palette(ldraw_dir):
    from legolization.color import default_palette
    try:
        if len(default_palette()):
            return 'existing_generated_palette'
    except (ImportError, FileNotFoundError):
        pass
    path = Path(ldraw_dir) / 'LDConfig.ldr'
    if not path.is_file():
        raise FileNotFoundError('缺少颜色库：' + str(path))
    from ldraw.colour import Colour
    module = types.ModuleType('ldraw.library.colours')
    for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        fields = line.split()
        if fields[:2] != ['0', '!COLOUR']:
            continue
        if len(fields) < 8 or 'CODE' not in fields or 'VALUE' not in fields:
            raise ValueError('LDConfig 中存在无效的颜色定义。')
        name = fields[2]
        code = int(fields[fields.index('CODE') + 1])
        rgb = fields[fields.index('VALUE') + 1]
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name):
            raise ValueError('颜色名称不适用于颜色模块：' + name)
        alpha = int(fields[fields.index('ALPHA') + 1]) if 'ALPHA' in fields else None
        setattr(module, name, Colour(code=code, name=name, rgb=rgb, alpha=alpha))
    # This shim is confined to this Python process: no site-packages/config edits.
    try:
        parent = importlib.import_module('ldraw.library')
    except ImportError:
        parent = types.ModuleType('ldraw.library'); parent.__path__ = []
        sys.modules['ldraw.library'] = parent
    sys.modules['ldraw.library.colours'] = module
    parent.colours = module
    default_palette.cache_clear()
    if not len(default_palette()):
        raise ValueError('LDConfig 没有可用的实心零件颜色。')
    return 'session_palette_from_LDConfig'

def check(settings, require_renderer=True):
    from .compat.api import load_api
    api = load_api()
    if not (settings.ldraw_dir / 'parts').is_dir():
        raise FileNotFoundError('LDraw 路径必须包含 parts：' + str(settings.ldraw_dir))
    if require_renderer and not settings.renderer.is_file():
        raise FileNotFoundError('未找到 LeoCAD：' + str(settings.renderer))
    palette = ensure_palette(settings.ldraw_dir)
    import PIL, reportlab
    print('Python：' + sys.version.split()[0] + '；解释器：' + sys.executable, flush=True)
    print('legolization：0.6.0；渲染器：' + str(settings.renderer), flush=True)
    print('LDraw：' + str(settings.ldraw_dir) + '；颜色库：' + palette, flush=True)
    return api

def main():
    parser = __import__('argparse').ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    from .config import read_settings
    check(read_settings(args.config))
    print('环境检查通过；没有导入或求解模型。')

if __name__ == '__main__':
    main()
