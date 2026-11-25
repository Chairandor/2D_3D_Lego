"""本地人体/手部关键点和食指指向控制；模型朝向只影响显示。

MediaPipe Pose/Hands: https://github.com/google-ai-edge/mediapipe/tree/v0.10.21
指向按未镜像的视频画面定义；旋转使用标记的右手系 Z-up 坐标。
"""
from __future__ import annotations

import math

import numpy as np


ACTIONS = {'left': '顺时针旋转', 'right': '逆时针旋转', 'up': '向后翻滚', 'down': '向前翻滚'}


class PointingRecognizer:
    """方向保持 0.35 秒后持续输出；方向消失或冲突立即输出停止。"""

    def __init__(self, hold_seconds: float = 0.35) -> None:
        self.hold_seconds = hold_seconds
        self.reset()

    def reset(self) -> None:
        """清除已确认的方向和保持进度。"""
        self.pending = ''
        self.pending_since = 0.0
        self.samples = 0
        self.last_seen = None
        self.progress = 0.0
        self.state = 'idle'

    def update(self, observations: dict[str, str], timestamp: float) -> str | None:
        """接受各手的食指方向，不使用位移或人体肩部可见性作门槛。"""
        directions = {direction for direction in observations.values() if direction in ACTIONS}
        if len(directions) > 1:
            self.pending, self.samples, self.progress, self.state = '', 0, 0, 'ambiguous'
            return None
        if not directions:
            self.pending, self.samples, self.progress = '', 0, 0
            self.state = 'idle'
            return None
        direction = next(iter(directions))
        if direction != self.pending or self.last_seen is None or timestamp - self.last_seen > 1.5:
            self.pending, self.pending_since, self.samples = direction, timestamp, 0
        self.last_seen = timestamp
        self.samples += 1
        self.progress = min((timestamp - self.pending_since) / self.hold_seconds, 1.0)
        self.state = 'holding'
        if self.progress < 1 or self.samples < 3:
            return None
        self.state = 'active'
        return direction

    def feedback(self) -> dict:
        """返回可以直接显示给用户的方向和确认进度。"""
        return dict(current_direction=self.pending if self.state != 'idle' else '',
                    hold_progress=round(self.progress if self.state != 'idle' else 0, 3),
                    pointing_state=self.state)


class ModelRotation:
    """绕包围盒中心旋转；左/右按从 +Z 俯视，后方为标记 +Y。"""

    def __init__(self, center: np.ndarray, speed_degrees: float = 60) -> None:
        self.center = np.asarray(center, dtype=float)
        if self.center.shape != (3,) or not np.isfinite(self.center).all():
            raise ValueError('模型中心必须为有限的三维坐标。')
        if not math.isfinite(speed_degrees) or not 5 <= speed_degrees <= 180:
            raise ValueError('旋转速度必须在 5–180 度/秒之间。')
        self.speed_degrees = speed_degrees
        self.rotation = np.eye(3)
        self.last_time = None
        self.active_direction = ''
        self.sequence = 0
        self.last_direction = ''

    def drive(self, direction: str | None, timestamp: float) -> bool:
        """更新持续控制方向；空方向停止，返回是否开始了新的旋转方向。"""
        if direction is not None and direction not in ACTIONS:
            raise ValueError('未知指向。')
        self.advance(timestamp)
        changed = bool(direction) and direction != self.active_direction
        self.active_direction = direction or ''
        if changed:
            self.sequence += 1
            self.last_direction = direction
        return changed

    def advance(self, timestamp: float) -> np.ndarray:
        """按实际经过时间积累角度，停转时保持朝向和模型中心不变。"""
        elapsed = min(max(timestamp - self.last_time, 0), 0.5) if self.last_time is not None else 0
        direction = self.active_direction
        if direction and elapsed:
            angle = math.radians(self.speed_degrees) * elapsed * (-1 if direction in ('left', 'up') else 1)
            c, s = math.cos(angle), math.sin(angle)
            delta = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]) if direction in ('left', 'right') else np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
            self.rotation = delta @ self.rotation
        self.last_time = timestamp
        result = np.eye(4)
        result[:3, :3] = self.rotation
        result[:3, 3] = self.center - self.rotation @ self.center
        return result

    def reset(self) -> None:
        """恢复显示朝向，不修改几何或源文件。"""
        self.rotation = np.eye(3)
        self.active_direction = ''
        self.last_time = None
        self.last_direction = ''

    def status(self) -> dict:
        """输出网页可追踪的动作序号和 AR 坐标系旋转矩阵。"""
        return dict(sequence=self.sequence, last_direction=self.last_direction,
                    last_action=ACTIONS.get(self.last_direction, ''),
                    control_mode='continuous', active_direction=self.active_direction,
                    is_rotating=bool(self.active_direction), speed_degrees_per_second=self.speed_degrees,
                    rotation_ar=self.rotation.round(8).tolist(), center_m=self.center.tolist())


def pointing_direction(points: np.ndarray, width: int, height: int) -> str | None:
    """食指伸直、其余三指收起时，按指根到指尖的屏幕向量分类。"""
    points = np.asarray(points, dtype=float)
    if points.shape != (21, 3) or not np.isfinite(points).all():
        return None
    pixels = points * [width, height, width]

    def extended(mcp: int, pip: int, tip: int) -> bool:
        proximal, distal = pixels[mcp] - pixels[pip], pixels[tip] - pixels[pip]
        denominator = np.linalg.norm(proximal) * np.linalg.norm(distal)
        return denominator > 1e-6 and np.dot(proximal, distal) / denominator < -0.65 and np.linalg.norm(pixels[tip] - pixels[0]) > 1.05 * np.linalg.norm(pixels[pip] - pixels[0])

    if not extended(5, 6, 8) or any(extended(mcp, pip, tip) for mcp, pip, tip in ((9, 10, 12), (13, 14, 16), (17, 18, 20))):
        return None
    vector = pixels[8, :2] - pixels[5, :2]
    palm_size = max(np.linalg.norm(pixels[9, :2] - pixels[0, :2]), 1)
    major = int(np.argmax(np.abs(vector)))
    if np.linalg.norm(vector) < max(12, 0.45 * palm_size) or abs(vector[major]) < 1.4 * abs(vector[1 - major]):
        return None
    return ('right' if vector[0] > 0 else 'left') if major == 0 else ('down' if vector[1] > 0 else 'up')


class SkeletonGestureTracker:
    """使用 wheel 自带模型离线运行 33 点人体骨架与双手 21 点跟踪。"""

    def __init__(self) -> None:
        try:
            import mediapipe as mp
            self.mp = mp
            self.pose = mp.solutions.pose.Pose(model_complexity=1, enable_segmentation=False,
                                              min_detection_confidence=0.6, min_tracking_confidence=0.6)
            self.hands = mp.solutions.hands.Hands(max_num_hands=2, model_complexity=1,
                                                 min_detection_confidence=0.65, min_tracking_confidence=0.65)
        except (ImportError, AttributeError) as exc:
            raise RuntimeError('手势环境不可用，请在独立 AR 环境执行 pip install -r requirements.txt（需 MediaPipe 0.10.21）。') from exc
        self.recognizer = PointingRecognizer()
        self.pose_result = None
        self.hand_result = None
        self.pointing_hands = []
        self.info = dict(mode='index-pointing', pose_detected=False, hands_detected=0, pointing_hands=0, pose_frames=0,
                         hand_frames=0, engine='mediapipe-' + mp.__version__, hint='伸出食指、收起其余三指，指向左 / 右 / 上 / 下')

    def process(self, frame: np.ndarray, timestamp: float) -> str | None:
        """在原始视频上提取骨架；不依赖模型或渲染像素。"""
        import cv2
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        self.pose_result = self.pose.process(rgb)
        self.hand_result = self.hands.process(rgb)
        height, width = frame.shape[:2]
        pose = self.pose_result.pose_landmarks
        observations = {}
        hand_landmarks = self.hand_result.multi_hand_landmarks or []
        self.info.update(pose_detected=pose is not None, hands_detected=len(hand_landmarks), pointing_hands=0)
        self.info['pose_frames'] += int(pose is not None)
        self.info['hand_frames'] += int(bool(hand_landmarks))
        self.pointing_hands = []
        for index, landmarks in enumerate(hand_landmarks):
            points = np.array([[p.x, p.y, p.z] for p in landmarks.landmark])
            direction = pointing_direction(points, width, height)
            if direction:
                observations[str(index)] = direction
                self.pointing_hands.append((landmarks, direction))
        self.info['pointing_hands'] = len(observations)
        event = self.recognizer.update(observations, timestamp)
        self.info.update(self.recognizer.feedback())
        state = self.recognizer.state
        self.info['hint'] = ('两只手方向不同，请只用一只手指向' if state == 'ambiguous' else
                             '正在持续旋转；收回食指即可停止' if state == 'active' else
                             '保持食指方向约 0.35 秒' if observations else
                             '伸出食指、收起其余三指，指向左 / 右 / 上 / 下')
        return event

    def draw(self, frame: np.ndarray) -> None:
        """把关键点和连线画回同一帧，方便判断是否跟踪到手和身体。"""
        draw = self.mp.solutions.drawing_utils
        if self.pose_result and self.pose_result.pose_landmarks:
            draw.draw_landmarks(frame, self.pose_result.pose_landmarks, self.mp.solutions.pose.POSE_CONNECTIONS,
                                draw.DrawingSpec(color=(95, 190, 105), thickness=2, circle_radius=2),
                                draw.DrawingSpec(color=(70, 150, 80), thickness=2))
        if self.hand_result:
            for hand in self.hand_result.multi_hand_landmarks or []:
                draw.draw_landmarks(frame, hand, self.mp.solutions.hands.HAND_CONNECTIONS,
                                    draw.DrawingSpec(color=(40, 200, 250), thickness=2, circle_radius=3),
                                    draw.DrawingSpec(color=(40, 160, 245), thickness=2))
        import cv2
        height, width = frame.shape[:2]
        for hand, direction in self.pointing_hands:
            start, end = [(int(hand.landmark[i].x * width), int(hand.landmark[i].y * height)) for i in (5, 8)]
            cv2.arrowedLine(frame, start, end, (40, 220, 100), 4, tipLength=0.25)
            cv2.putText(frame, direction.upper(), (max(0, min(end[0], width - 70)), max(20, min(end[1], height - 8))),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (40, 220, 100), 2)

    def close(self) -> None:
        """释放推理图与本机 CPU 资源。"""
        self.pose.close()
        self.hands.close()
