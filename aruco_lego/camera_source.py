"""AR、骨架、手势与标定共用的相机入口：Gemini SDK 彩色流 / OpenCV。

Orbbec C ABI 依据 Lab2 的 SDK 1.8.3 头文件，厂商 SDK 保留在原安装目录。
参考：https://github.com/orbbec/OrbbecSDK 。不修改驱动、固件或 Lab2 文件。
"""
from __future__ import annotations

import ctypes as ct
import os
from pathlib import Path

import cv2
import numpy as np


class Intrinsic(ct.Structure):
    _pack_ = 1
    _fields_ = [('fx', ct.c_float), ('fy', ct.c_float), ('cx', ct.c_float), ('cy', ct.c_float),
                ('width', ct.c_int16), ('height', ct.c_int16)]


class Distortion(ct.Structure):
    _pack_ = 1
    _fields_ = [(name, ct.c_float) for name in ('k1', 'k2', 'k3', 'k4', 'k5', 'k6', 'p1', 'p2')]


class Transform(ct.Structure):
    _pack_ = 1
    _fields_ = [('rotation', ct.c_float * 9), ('translation', ct.c_float * 3)]


class CameraParameters(ct.Structure):
    _pack_ = 1
    _fields_ = [('depth', Intrinsic), ('rgb', Intrinsic), ('depth_distortion', Distortion),
                ('rgb_distortion', Distortion), ('transform', Transform), ('mirrored', ct.c_bool)]


def find_gemini_sdk(explicit: str = '') -> Path:
    """定位用户已有 SDK，支持 SDK 根目录、lib 目录或 DLL 完整路径。"""
    choices = []
    if value := explicit or os.environ.get('ORBBEC_SDK_DIR'):
        root = Path(value).expanduser()
        choices = [root, root / 'OrbbecSDK.dll', root / 'SDK/lib/OrbbecSDK.dll', root / 'lib/OrbbecSDK.dll']
    else:
        lab = Path(os.environ.get('GEMINI_LAB2_ROOT', Path.home() / 'Desktop/大三上/3D视觉/Lab2'))
        choices = [lab / '.setup/tools/orbbec-1.8.3/OrbbecSDK_v1.8.3/SDK/lib/OrbbecSDK.dll',
                   lab / '.setup/checks/orbbec183-vs2019/bin/Release/OrbbecSDK.dll']
    for path in choices:
        if path.is_file() and path.name.lower() == 'orbbecsdk.dll':
            return path.resolve()
    raise FileNotFoundError('未找到 Gemini 的 Orbbec SDK 1.x。请将 ORBBEC_SDK_DIR 指向 Lab2 的 SDK/lib 目录或 OrbbecSDK.dll。')


class OrbbecSDK:
    """对厂商 DLL 做窄范围 ctypes 调用，统一处理 C 错误与指针类型。"""

    def __init__(self, path: str = '') -> None:
        self.path = find_gemini_sdk(path)
        if os.name != 'nt' or ct.sizeof(ct.c_void_p) != 8:
            raise RuntimeError('此 Lab2 SDK 为 Windows x64，请使用 64 位 Windows Python。')
        self.dll_directory = os.add_dll_directory(str(self.path.parent))
        self.lib = ct.CDLL(str(self.path))
        pointer, integer, unsigned = ct.c_void_p, ct.c_int, ct.c_uint32
        specs = {
            'ob_create_context': (pointer, []), 'ob_query_device_list': (pointer, [pointer]),
            'ob_device_list_device_count': (unsigned, [pointer]),
            'ob_device_list_get_device_name': (ct.c_char_p, [pointer, unsigned]),
            'ob_device_list_get_device_serial_number': (ct.c_char_p, [pointer, unsigned]),
            'ob_device_list_get_device': (pointer, [pointer, unsigned]),
            'ob_create_pipeline_with_device': (pointer, [pointer]),
            'ob_create_config': (pointer, []), 'ob_config_enable_stream': (None, [pointer, pointer]),
            'ob_pipeline_start_with_config': (None, [pointer, pointer]), 'ob_pipeline_stop': (None, [pointer]),
            'ob_pipeline_get_stream_profile_list': (pointer, [pointer, integer]),
            'ob_stream_profile_list_count': (unsigned, [pointer]),
            'ob_stream_profile_list_get_profile': (pointer, [pointer, integer]),
            'ob_stream_profile_format': (integer, [pointer]),
            'ob_video_stream_profile_width': (unsigned, [pointer]),
            'ob_video_stream_profile_height': (unsigned, [pointer]),
            'ob_video_stream_profile_fps': (unsigned, [pointer]),
            'ob_pipeline_wait_for_frameset': (pointer, [pointer, unsigned]),
            'ob_frameset_color_frame': (pointer, [pointer]), 'ob_frame_data': (pointer, [pointer]),
            'ob_frame_data_size': (unsigned, [pointer]), 'ob_frame_format': (integer, [pointer]),
            'ob_video_frame_width': (unsigned, [pointer]), 'ob_video_frame_height': (unsigned, [pointer]),
            'ob_pipeline_get_camera_param': (CameraParameters, [pointer]),
            'ob_pipeline_get_camera_param_with_profile': (CameraParameters, [pointer, unsigned, unsigned, unsigned, unsigned]),
            'ob_set_logger_severity': (None, [integer]),
        }
        for name in ('context', 'device_list', 'device', 'pipeline', 'config', 'stream_profile_list', 'stream_profile', 'frame'):
            specs['ob_delete_' + name] = (None, [pointer])
        for name, (result, args) in specs.items():
            function = getattr(self.lib, name)
            function.restype, function.argtypes = result, args + [ct.POINTER(pointer)]
        self.lib.ob_error_message.restype = ct.c_char_p
        self.lib.ob_error_message.argtypes = [pointer]
        self.lib.ob_delete_error.restype = None
        self.lib.ob_delete_error.argtypes = [pointer]
        self.call('ob_set_logger_severity', 3)

    def call(self, name: str, *args):
        """调用已声明的 SDK 函数；错误对象读取后立即释放。"""
        error = ct.c_void_p()
        result = getattr(self.lib, name)(*args, ct.byref(error))
        if error.value:
            message = self.lib.ob_error_message(error)
            text = message.decode('utf-8', errors='replace') if message else 'unknown error'
            self.lib.ob_delete_error(error)
            raise RuntimeError(f'Gemini SDK {name}: {text}')
        return result

    def close(self) -> None:
        """关闭 DLL 搜索目录句柄，不卸载仍可能被原生线程使用的 DLL。"""
        self.dll_directory.close()


FORMAT_NAMES = {0: 'YUYV', 1: 'YUY2', 2: 'UYVY', 3: 'NV12', 4: 'NV21', 5: 'MJPEG',
                22: 'RGB', 23: 'BGR', 25: 'BGRA'}


def decode_color(data: bytes, width: int, height: int, pixel_format: int) -> np.ndarray:
    """将 Gemini 原始彩色像素转换成同一个 OpenCV BGR 接口。"""
    raw = np.frombuffer(data, np.uint8)
    if pixel_format == 5:
        frame = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    elif pixel_format in (22, 23, 25):
        channels = 4 if pixel_format == 25 else 3
        if len(raw) < width * height * channels or len(raw) % height:
            raise ValueError('Gemini 彩色帧缓冲区尺寸无效。')
        frame = raw.reshape(height, -1)[:, :width * channels].reshape(height, width, channels).copy()
        if pixel_format == 22:
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        elif pixel_format == 25:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    elif pixel_format in (0, 1, 2):
        frame = raw.reshape(height, width, 2)
        frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_UYVY if pixel_format == 2 else cv2.COLOR_YUV2BGR_YUY2)
    elif pixel_format in (3, 4):
        frame = cv2.cvtColor(raw.reshape(height * 3 // 2, width),
                             cv2.COLOR_YUV2BGR_NV12 if pixel_format == 3 else cv2.COLOR_YUV2BGR_NV21)
    else:
        raise ValueError(f'不支持的 Gemini 彩色格式 {pixel_format}。')
    if frame is None or frame.shape != (height, width, 3):
        raise ValueError('Gemini 彩色图解码失败或尺寸与流配置不一致。')
    return frame


class GeminiCamera:
    """只打开指定 Gemini 的彩色传感器，不占用笔记本或 Astra 相机。"""

    def __init__(self, index: int = 0, width: int = 640, height: int = 480, sdk_path: str = '') -> None:
        self.sdk = OrbbecSDK(sdk_path)
        self.handles = []
        self.pipeline = None
        self.streaming = False
        self.info = {'source': 'gemini', 'backend': 'orbbec-sdk-v1', 'sdk_dll': str(self.sdk.path)}
        self.camera_parameters = None
        try:
            context = self._keep('context', self.sdk.call('ob_create_context'))
            devices = self._keep('device_list', self.sdk.call('ob_query_device_list', context))
            found = []
            for i in range(self.sdk.call('ob_device_list_device_count', devices)):
                name = self.sdk.call('ob_device_list_get_device_name', devices, i).decode('utf-8', errors='replace')
                if 'gemini' in name.lower():
                    found.append((i, name))
            if not 0 <= index < len(found):
                raise RuntimeError(f'未找到 Gemini 设备 {index}（当前 {len(found)} 台）。请连接 Lab2 的 Gemini 2，关闭 Orbbec Viewer / InfraredViewer 后重试。')
            device_index, name = found[index]
            serial = self.sdk.call('ob_device_list_get_device_serial_number', devices, device_index).decode('utf-8', errors='replace')
            self.info.update(name=name, serial_number=serial, device_index=index)
            device = self._keep('device', self.sdk.call('ob_device_list_get_device', devices, device_index))
            self.pipeline = self._keep('pipeline', self.sdk.call('ob_create_pipeline_with_device', device))
            profiles = self._keep('stream_profile_list', self.sdk.call('ob_pipeline_get_stream_profile_list', self.pipeline, 2))
            choices = []
            for i in range(self.sdk.call('ob_stream_profile_list_count', profiles)):
                profile = self._keep('stream_profile', self.sdk.call('ob_stream_profile_list_get_profile', profiles, i))
                fmt = self.sdk.call('ob_stream_profile_format', profile)
                if fmt not in FORMAT_NAMES:
                    continue
                w = self.sdk.call('ob_video_stream_profile_width', profile)
                h = self.sdk.call('ob_video_stream_profile_height', profile)
                fps = self.sdk.call('ob_video_stream_profile_fps', profile)
                score = abs(w - width) + abs(h - height) + abs(fps - 30) * 5 + (0 if fmt in (22, 23) else 5)
                choices.append((score, i, profile, w, h, fps, fmt))
            if not choices:
                raise RuntimeError('此 Gemini 没有可解码的彩色流。请检查 USB3 连接和 SDK；不能将红外图假称为彩色图。')
            _, _, profile, w, h, fps, fmt = min(choices)
            self.info.update(width=w, height=h, fps=fps, format=FORMAT_NAMES[fmt])
            config = self._keep('config', self.sdk.call('ob_create_config'))
            self.sdk.call('ob_config_enable_stream', config, profile)
            self.sdk.call('ob_pipeline_start_with_config', self.pipeline, config)
            self.streaming = True
            try:
                self.camera_parameters = self.sdk.call('ob_pipeline_get_camera_param_with_profile', self.pipeline, w, h, 0, 0)
            except RuntimeError:
                try:
                    self.camera_parameters = self.sdk.call('ob_pipeline_get_camera_param', self.pipeline)
                except RuntimeError as error:
                    self.info['intrinsics_warning'] = str(error)
        except Exception:
            self.release()
            raise

    def _keep(self, kind: str, handle):
        if not handle:
            raise RuntimeError('Gemini SDK 返回了空句柄：' + kind)
        self.handles.append((kind, handle))
        return handle

    def read(self) -> tuple[bool, np.ndarray | None]:
        """有限等待彩色帧，复制数据后释放 SDK 帧对象。"""
        frameset = self.sdk.call('ob_pipeline_wait_for_frameset', self.pipeline, 1000)
        if not frameset:
            return False, None
        color = None
        try:
            color = self.sdk.call('ob_frameset_color_frame', frameset)
            if not color:
                return False, None
            width = self.sdk.call('ob_video_frame_width', color)
            height = self.sdk.call('ob_video_frame_height', color)
            fmt = self.sdk.call('ob_frame_format', color)
            size = self.sdk.call('ob_frame_data_size', color)
            pointer = self.sdk.call('ob_frame_data', color)
            if not pointer or not 0 < size <= 128 * 1024 * 1024:
                raise RuntimeError('Gemini 返回空帧或异常大小的彩色缓冲区。')
            data = ct.string_at(pointer, size)
            return True, decode_color(data, width, height, fmt)
        finally:
            try:
                if color:
                    self.sdk.call('ob_delete_frame', color)
            finally:
                self.sdk.call('ob_delete_frame', frameset)

    def intrinsics(self):
        """使用当前彩色流的工厂内参；不使用深度相机内参。"""
        parameters = self.camera_parameters
        if parameters is None or parameters.mirrored:
            return None
        intrinsic, distortion = parameters.rgb, parameters.rgb_distortion
        width, height = self.info['width'], self.info['height']
        if min(intrinsic.width, intrinsic.height, intrinsic.fx, intrinsic.fy) <= 0:
            return None
        if abs(intrinsic.width / intrinsic.height - width / height) > 0.01:
            return None  # Cropped modes need mode-specific calibration, not guessed scaling.
        sx, sy = width / intrinsic.width, height / intrinsic.height
        K = np.array([[intrinsic.fx * sx, 0, intrinsic.cx * sx], [0, intrinsic.fy * sy, intrinsic.cy * sy], [0, 0, 1]], dtype=float)
        dist = np.array([distortion.k1, distortion.k2, distortion.p1, distortion.p2,
                         distortion.k3, distortion.k4, distortion.k5, distortion.k6], dtype=float)
        if not np.isfinite(K).all() or not np.isfinite(dist).all():
            return None
        return K, dist

    def release(self) -> None:
        """停止彩色流，并按依赖顺序释放本实例的 SDK 对象。"""
        if self.streaming:
            try:
                self.sdk.call('ob_pipeline_stop', self.pipeline)
            except RuntimeError:
                pass
            self.streaming = False
        while self.handles:
            kind, handle = self.handles.pop()
            try:
                self.sdk.call('ob_delete_' + kind, handle)
            except RuntimeError:
                pass
        self.sdk.close()


class OpenCVCamera:
    """保留普通相机入口，也输出一致的 BGR 帧。"""

    def __init__(self, index: int, width: int, height: int, api: str = 'auto') -> None:
        self.cap = None
        apis = {'auto': cv2.CAP_ANY, 'dshow': cv2.CAP_DSHOW, 'msmf': cv2.CAP_MSMF}
        for name in [api] if api != 'auto' else (['dshow', 'msmf'] if os.name == 'nt' else ['auto']):
            cap = cv2.VideoCapture(index, apis[name])
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self.cap = cap
                self.info = dict(source='opencv', backend=name, name=f'OpenCV camera {index}')
                break
            cap.release()
        if self.cap is None:
            raise RuntimeError(f'无法打开普通摄像头 {index}。请检查权限、连接或相机占用。')

    def read(self):
        return self.cap.read()

    def intrinsics(self):
        return None

    def release(self) -> None:
        self.cap.release()


def open_camera(source: str, index: int = 0, width: int = 640, height: int = 480,
                api: str = 'auto', sdk_path: str = '') -> GeminiCamera | OpenCVCamera:
    """统一入口；指定 Gemini 时失败即报错，不静默换到笔记本相机。"""
    if source == 'gemini':
        return GeminiCamera(index, width, height, sdk_path)
    if source == 'opencv':
        return OpenCVCamera(index, width, height, api)
    raise ValueError('相机来源必须是 gemini 或 opencv。')
