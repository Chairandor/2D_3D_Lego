"""工作台中的 LDraw 选择、预览和摄像头任务；重依赖运行在独立环境。"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time

from project_config import resolve_core_root, terminate_process_tree, venv_executable
from ui_3d_jobs import find_ldraw


def ldraw_upload_text(raw: bytes) -> str:
    if not raw or len(raw) > 32 * 1024 * 1024:
        raise ValueError('模型文件为空或超过 32 MB。')
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('LDraw 文件须为 UTF-8 文本；请重新导出为 LDR / MPD。') from exc


def validate_camera_options(data: dict) -> dict:
    """校验界面的厘米参数，并转换为 AR 使用的米制参数。"""
    result = {}
    for name, low, high, default in [('camera', 0, 20, 0), ('marker_id', 0, 49, 0),
                                     ('marker_size_cm', 0.1, 200, 6), ('fit_width_cm', 0.1, 200, 9),
                                     ('gesture_angle', 5, 90, 30)]:
        try:
            value = float(data.get(name, default))
        except (ValueError, TypeError) as exc:
            raise ValueError(f'{name} 必须是有效数字。') from exc
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{name} 必须在 {low}–{high} 范围内。')
        if name in ('camera', 'marker_id'):
            if not value.is_integer():
                raise ValueError('相机编号和标记 ID 必须是整数。')
            result[name] = int(value)
        else:
            result[name] = value
    for name in ('gestures', 'show_skeleton'):
        value = data.get(name, True)
        if not isinstance(value, bool):
            raise ValueError(f'{name} 必须是布尔值。')
        result[name] = value
    source = data.get('camera_source', 'gemini')
    if source not in ('gemini', 'opencv'):
        raise ValueError('相机来源必须为 gemini 或 opencv。')
    result['camera_source'] = source
    return result


class ArucoService:
    """管理一个独立 AR 任务，支持停止、刷新恢复和真实模型预览。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.core = resolve_core_root(self.root)
        self.ar_root = Path(os.environ.get('ARUCO_ROOT', self.core / 'aruco_lego')).resolve()
        self.python = Path(os.environ.get('ARUCO_PYTHON', venv_executable(self.ar_root / '.venv', 'python')))
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self.phase = 'idle'
        self.message = '选择一个 LDR / MPD 模型'
        self.model: Path | None = None
        self.preview_dir: Path | None = None
        self.live_dir: Path | None = None
        self.logs: deque[str] = deque(maxlen=100)
        self.options = validate_camera_options({})
        self.started = 0.0
        self.studio_colors: str | None = None
        self.board_mode = False
        self.placements: list[dict] = []

    def _busy(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _model_path(self, value: str) -> Path:
        path = Path(value).resolve()
        if not any(path.is_relative_to(root) for root in (self.root, self.core)):
            raise ValueError('请使用“选择文件”导入项目外的 LDraw 模型。')
        if not path.is_file() or path.suffix.lower() not in {'.ldr', '.mpd'}:
            raise ValueError('请选择存在的 .ldr 或 .mpd 文件。')
        return path

    def _colors(self) -> str:
        if self.studio_colors is not None:
            return self.studio_colors
        self.studio_colors = os.environ.get('ARUCO_STUDIO_COLORS', '')
        if not self.studio_colors and os.name == 'nt':
            # Resolve the installed Studio shortcut rather than assuming a drive.
            script = "$s=Join-Path $env:ProgramData 'Microsoft\\Windows\\Start Menu\\Programs\\Studio 2.0.lnk'; if(Test-Path -LiteralPath $s){$t=(New-Object -ComObject WScript.Shell).CreateShortcut($s).TargetPath; $p=Join-Path (Split-Path -Parent $t) 'data\\StudioColorDefinition.txt'; if(Test-Path -LiteralPath $p){[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Console]::Write($p)}}"
            try:
                result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                                        capture_output=True, text=True, encoding='utf-8', timeout=10,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode == 0:
                    self.studio_colors = result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
        return self.studio_colors

    def _dependency_args(self) -> list[str]:
        result = []
        library = find_ldraw(self.root)
        if library.is_dir():
            result += ['--ldraw-dir', str(library)]
        if colors := self._colors():
            result += ['--studio-colors', colors]
        return result

    def _launch(self, command: list[str], phase: str) -> None:
        if not self.python.is_file() or not (self.ar_root / 'aruco_lego_ar.py').is_file():
            raise ValueError('未找到 AR 环境。请配置 ARUCO_ROOT 和 ARUCO_PYTHON，或先按 aruco_lego/README.md 安装。')
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'
        self.logs.clear()
        self.started = time.monotonic()
        process = subprocess.Popen([str(self.python), '-u', *command], cwd=self.root, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace',
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                   start_new_session=os.name != 'nt')
        self.process = process
        self.phase = phase
        self.reader = threading.Thread(target=self._collect, args=(process, phase), daemon=True)
        self.reader.start()

    def _collect(self, process: subprocess.Popen, phase: str) -> None:
        for line in process.stdout:
            with self.lock:
                self.logs.append(line.rstrip())
        process.stdout.close()
        code = process.wait()
        with self.lock:
            if self.process is not process:
                return
            stopped = self.phase == 'stopping'
            self.phase = 'stopped' if stopped else ('ready' if code == 0 and phase == 'preparing' else 'stopped' if code == 0 else 'failed')
            self.message = {'ready': '模型已就绪，可以旋转查看或启动 AR', 'stopped': '摄像头已停止，模型仍可查看',
                            'failed': '任务失败，请查看下方提示与日志'}[self.phase]

    def bootstrap(self) -> dict:
        """返回可用示例与环境状态，不打开相机、不下载文件。"""
        presets = []
        for name in ('chicken.ldr', 'jackjack失败版.ldr'):
            path = self.core / name
            if path.is_file():
                presets.append(dict(label=name, path=str(path)))
        return dict(presets=presets, available=self.python.is_file() and (self.ar_root / 'aruco_lego_ar.py').is_file())

    def select(self, value: str) -> dict:
        """载入一个已有模型，并在后台生成真实几何的网页预览。"""
        with self.lock:
            if self._busy():
                raise RuntimeError('请先停止当前预览任务，再切换模型。')
            model = self._model_path(value)
            stamp = time.strftime('%Y%m%d_%H%M%S') + '_' + secrets.token_hex(4)
            output = self.root / 'runs' / 'aruco' / stamp
            self._launch([str(self.root / 'aruco_worker.py'), '--ar-root', str(self.ar_root),
                          '--model', str(model), '--output', str(output), *self._dependency_args()], 'preparing')
            self.model, self.preview_dir, self.live_dir = model, output, None
            self.board_mode = False
            self.message = '正在读取零件和颜色，生成模型预览……'
            return self.status()

    def upload(self, name: str, raw: bytes) -> dict:
        """保留上传文件的独立副本，拒绝不支持或过大的输入。"""
        if Path(name).suffix.lower() not in {'.ldr', '.mpd'}:
            raise ValueError('只支持 .ldr 和 .mpd 文件。')
        ldraw_upload_text(raw)
        with self.lock:
            if self._busy():
                raise RuntimeError('请先停止当前任务，再选择模型。')
            safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip(' .')
            directory = self.root / 'ui_uploads' / 'aruco' / secrets.token_hex(8)
            directory.mkdir(parents=True)
            path = directory / safe
            path.write_bytes(raw)
            return self.select(str(path))

    def start(self, data: dict) -> dict:
        """使用当前已验明的模型开启本机摄像头与网页视频输出。"""
        options = validate_camera_options(data)
        with self.lock:
            if self._busy():
                raise RuntimeError('当前已有任务，请先停止。')
            if self.model is None or self.preview_dir is None or not (self.preview_dir / 'model.json').is_file():
                raise ValueError('请先选择模型，等待预览加载完成。')
            report = json.loads((self.preview_dir / 'model.json').read_text(encoding='utf-8'))
            if hashlib.sha256(self.model.read_bytes()).hexdigest() != report['source_sha256']:
                raise ValueError('源模型已变化，请重新载入后再启动摄像头。')
            live = self.preview_dir / ('live_' + secrets.token_hex(4))
            command = self._camera_command(options, live)
            command += ['--model', str(self.model), '--fit-width', str(options['fit_width_cm'] / 100)]
            if options['gestures']:
                command += ['--gestures', '--gesture-angle', str(options['gesture_angle'])]
            if not options['show_skeleton']:
                command += ['--no-skeleton']
            self._launch(command, 'running')
            self.options, self.live_dir = options, live
            self.board_mode = False
            self.message = '正在打开摄像头……'
            return self.status()

    def _camera_command(self, options: dict, live: Path) -> list[str]:
        command = [str(self.ar_root / 'aruco_lego_ar.py'),
                   '--camera-source', options['camera_source'],
                   '--camera', str(options['camera']), '--marker-id', str(options['marker_id']),
                   '--marker-size', str(options['marker_size_cm'] / 100),
                   '--no-window', '--log-dir', str(live), '--frame-output', str(live / 'frame.jpg'),
                   '--stop-file', str(live / 'stop.request'), '--control-file', str(live / 'control.json'),
                   *self._dependency_args()]
        if calibration := os.environ.get('ARUCO_CALIBRATION'):
            command += ['--calibration', str(Path(calibration).expanduser().resolve())]
        return command

    def start_board(self, data: dict) -> dict:
        options = validate_camera_options({'camera_source': 'opencv', 'marker_size_cm': 5,
                                           **data, 'gestures': False, 'show_skeleton': False})
        with self.lock:
            if self._busy():
                raise RuntimeError('摄像头已被当前 AR 任务使用。')
            live = self.root / 'runs' / 'aruco' / ('board_' + secrets.token_hex(8))
            self._launch(self._camera_command(options, live) + ['--board'], 'running')
            self.board_mode = True
            self.placements = []
            self.options, self.live_dir = options, live
            self.message = '正在打开摄像头……'
            return self.status()

    def place_on_board(self, path: Path, label: str, data: dict) -> dict:
        try:
            x_cm, y_cm, angle = (float(data[key]) for key in ('x_cm', 'y_cm', 'angle'))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('位置和朝向须为有效数字。') from exc
        if not all(map(math.isfinite, (x_cm, y_cm, angle))):
            raise ValueError('位置和朝向须为有效数字。')
        if path.suffix.lower() not in {'.mpd', '.ldr'} or not path.is_file():
            raise ValueError('找不到这张 LEGO 卡片对应的模型。')
        with self.lock:
            if not self.board_mode or not self._busy() or self.phase != 'running' or self.live_dir is None:
                raise RuntimeError('请先开启图片页摄像头。')
            control = self.live_dir / 'control.json'
            if not control.parent.is_dir():
                raise RuntimeError('摄像头正在启动，请稍后再试。')
            placement = dict(id=secrets.token_hex(8), path=str(path), label=label,
                             x_cm=x_cm, y_cm=y_cm, angle=angle % 360)
            entries = [*self.placements, placement]
            temporary = control.with_suffix('.tmp')
            temporary.write_text(json.dumps({'placements': entries}, ensure_ascii=False), encoding='utf-8')
            temporary.replace(control)
            self.placements = entries
            return self.status()

    def reset_rotation(self) -> dict:
        """向当前相机任务发送显示朝向重置，不改动模型文件。"""
        with self.lock:
            if self.board_mode or not self._busy() or self.live_dir is None or self.phase != 'running':
                raise ValueError('请先启动摄像头，再恢复模型朝向。')
            self.live_dir.mkdir(parents=True, exist_ok=True)
            path = self.live_dir / 'control.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'reset_rotation': secrets.token_hex(8)}), encoding='utf-8')
            temporary.replace(path)
            return self.status()

    def stop(self, *, board_only: bool = False) -> None:
        """优先让渲染进程自行释放摄像头，超时仅终止本服务启动的任务。"""
        with self.lock:
            if board_only and not self.board_mode:
                return
            process = self.process
            if process is None or process.poll() is not None:
                return
            self.phase = 'stopping'
            if self.live_dir:
                self.live_dir.mkdir(parents=True, exist_ok=True)
                (self.live_dir / 'stop.request').touch()
        try:
            process.wait(timeout=3 if self.live_dir else 0.1)
        except subprocess.TimeoutExpired:
            terminate_process_tree(process)
            process.wait(timeout=5)
        if self.reader:
            self.reader.join(timeout=1)

    def asset(self, kind: str) -> Path | None:
        """仅开放本服务明确选择的模型与本次运行产物。"""
        with self.lock:
            paths = {'model': self.model,
                     'preview': self.preview_dir / 'preview.glb' if self.preview_dir else None,
                     'frame': self.live_dir / 'frame.jpg' if self.live_dir else None,
                     'snapshot': self.live_dir / 'first_ar_frame.png' if self.live_dir else None}
            path = paths.get(kind)
            return path if path and path.is_file() else None

    def status(self) -> dict:
        """返回任务、识别与渲染的分别计数，页面刷新不会启动新任务。"""
        with self.lock:
            report, camera = {}, {}
            try:
                if self.preview_dir and (self.preview_dir / 'model.json').is_file():
                    report = json.loads((self.preview_dir / 'model.json').read_text(encoding='utf-8'))
                if self.live_dir and (self.live_dir / 'status.json').is_file():
                    camera = json.loads((self.live_dir / 'status.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
            preview = self.asset('preview') is not None and bool(report)
            message = self.message
            if self.phase == 'running' and camera.get('camera_opened'):
                name = camera.get('camera_device', {}).get('name', '摄像头')
                if self.board_mode and camera.get('marker_tracked'):
                    message = '已识别硬板'
                else:
                    message = '已识别标记并显示 LEGO' if camera.get('overlay_pixels', 0) else f'{name} 已打开，寻找 ID {self.options["marker_id"]}'
            error = camera.get('error') or ('\n'.join(self.logs)[-1600:] if self.phase == 'failed' else '')
            return dict(phase=self.phase, running=self._busy(), message=message,
                        model_name=self.model.name if self.model else '', model_path=str(self.model or ''),
                        preview_url='/api/ar/file?kind=preview&v=' + (report.get('source_sha256', '')) if preview else '',
                        model_url='/api/ar/file?kind=model' if self.model else '',
                        frame_available=self.asset('frame') is not None,
                        snapshot_url='/api/ar/file?kind=snapshot' if self.asset('snapshot') else '',
                        report=report, camera=camera, options=self.options, board_mode=self.board_mode,
                        elapsed=round(time.monotonic() - self.started, 1) if self.started else 0,
                        error=error, logs='\n'.join(self.logs))
