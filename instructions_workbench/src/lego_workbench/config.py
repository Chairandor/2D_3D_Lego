"""All machine-specific paths live in JSON, not in the analysis modules."""
from dataclasses import dataclass
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class Settings:
    output_root: Path
    reuse_roots: tuple[Path, ...]
    renderer: Path
    ldraw_dir: Path
    ground: bool
    step_size: int
    policy: str
    models: dict

def read_settings(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    def resolve(value):
        p = Path(value).expanduser()
        return p if p.is_absolute() else (PROJECT / p).resolve()
    size = data.get('step_size', 7)
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 10:
        raise ValueError('step_size 必须是 1–10 的整数。')
    policy = data.get('stability_policy', 'warn')
    if policy not in ('warn', 'strict'):
        raise ValueError('stability_policy 必须为 warn 或 strict。')
    if not isinstance(data.get('ground', True), bool):
        raise ValueError('ground 必须是 JSON 布尔值 true 或 false。')
    models = {}
    for key, row in data['models'].items():
        models[key] = dict(row, path=resolve(row['path']))
    return Settings(resolve(data['output_root']), tuple(resolve(p) for p in data.get('reuse_roots', [])),
                    resolve(data['renderer']), resolve(data['ldraw_dir']),
                    bool(data.get('ground', True)), size, policy, models)
