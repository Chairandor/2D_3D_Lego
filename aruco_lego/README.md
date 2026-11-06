# 用 ArUco 码展示你的乐高模型（Marker-based LEGO AR）

## 仓库内使用

源码随 `2D_3D_Lego` 仓库提供，安装与路径配置见 [3D / ArUco 部署](../docs/SETUP_3D_AR.md)。合并 UI 位于本目录上一级的 `pipeline_ui.py`；从仓库根目录运行 `./start_aruco_ui.ps1`。

`.venv`、相机画面、标定文件、运行日志、LDraw 零件库和原始 LDR 模型不随源码提供。可在网页中上传自己的 LDR / MPD；独立窗口使用 `python aruco_lego_ar.py` 可显示程序生成的红砖，`start_ar.ps1 -Model demo` 对应此模式。下文中的绝对路径、示例模型和验收记录描述原开发机环境。

## 本机已配置入口（2026-11-01）

工作目录是 `C:\Users\19653\Desktop\LEGOLIZER\aruco_lego`。已在 `.venv` 中用 Python 3.10.21 安装依赖；`requirements-lock.txt` 记录本次实际版本。

**图形界面已合并进 `../2D_3D_Lego/pipeline_ui.py`**：在 `2D_3D_Lego` 目录运行 `./start_aruco_ui.ps1`，打开原工作台的“ArUco 预览”标签，选择 LDR / MPD 后即可旋转查看并启动网页内摄像头 AR。参数、停止、模型切换和下载都在同一页面完成，沿用该项目原有配色与卡片。下面的 `start_ar.ps1` 仍保留为独立 OpenCV 窗口入口。

在此目录的 PowerShell 中运行：

```powershell
.\start_ar.ps1                           # 默认根目录 chicken.ldr
.\start_ar.ps1 -Model jackjack            # 根目录 jackjack失败版.ldr
.\start_ar.ps1 -Model demo               # 原红色示例砖
.\start_ar.ps1 -MarkerSize 0.08           # 黑色正方形实测边长为 8 cm 时
```

切换模型前在当前窗口按 `q` 退出，释放摄像头。启动器使用摄像头 0、DICT_4X4_50、ID 0、黑边长 6 cm；可用 `-Camera`、`-MarkerId`、`-FitWidth`、`-Calibration` 调整。关闭窗口或按 `q` / ESC 退出。

窗口显示 `ID 0: searching` 表示尚未完成该码的叠加，`AR OK` 表示本帧检测到码、求得位姿且渲染出模型像素。请让黑色方形四角和周围白边完整进入镜头，离镜头远一些以容纳模型全貌。

每次启动写入独立的 `runs/live_*`：`status.json` 分别记录读帧数、识别帧数、渲染帧数；首次成功叠加时保存本机画面 `first_ar_frame.png`。不会上传相机画面。首次相机失败记录不代表设备不可用；本次在沙箱外已确认摄像头 0 能读帧。

现在可直接读取 `.ldr/.mpd`，按 LDraw 的 -Y 向上转为 AR 的 +Z 向上，再居中、落地、缩放；无需先转 GLB。使用本机已有零件库（`LDRAWDIR` 或 pyldraw3 缓存），也可通过 `--ldraw-dir` 指定。解析真实零件三角形/四边形、层级变换、继承色和直接色；不渲染 LDraw 边线、贴图或特殊材质效果，不作装配/稳定性认证。缺失零件或颜色会报错。

`jackjack失败版.ldr` 有 Studio 专用色号 `100241`；启动器从已安装 Studio 的开始菜单快捷方式定位其 `data/StudioColorDefinition.txt`，仅补齐官方库缺失的颜色。也可用 `-StudioColors '完整文件路径'` 指定，不修改原模型或原零件库。

验证命令（从项目根目录运行，输出目录须尚不存在）：

```powershell
.\aruco_lego\.venv\Scripts\python.exe -m pip check
.\aruco_lego\.venv\Scripts\python.exe -m unittest discover -s aruco_lego -p 'test_*.py' -v
.\aruco_lego\.venv\Scripts\python.exe aruco_lego/verify_ar.py --output aruco_lego/runs/verify_new chicken.ldr
```

`verify_ar.py` 是标记图片透视变换→检测→位姿→真实模型→OpenGL 的离线集成测试，不能代替打印码的摄像头验收。双模型测试还需提供 `--studio-colors`。

2026-11-01 实机验收：摄像头 0（DirectShow，640×480）识别到打印码 ID 0，并成功叠加 `chicken.ldr`；用户确认已看到小鸡。核对时已有 728 帧识别及成功渲染，未报告错误。证据为 `runs/live_chicken_20261101_02/status.json`、同名 stdout 日志和 `first_ar_frame.png`。历史记录也出现过 ID 17，但本次成功叠加的目标为 ID 0。实际打印黑边尺寸尚未测量，相机内参仍为估计值；本次验证识别和显示，不验证真实比例与标定精度。Jackjack 目前仅完成离线叠加验证。

## 1. 安装

本机已经配置 `.venv`，直接用上面的启动器。重建环境时，使用已有的 Python 3.10–3.12 创建独立环境：

```powershell
& 'C:\Users\19653\miniforge3\envs\legolizer\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

如电脑里已经安装多个 OpenCV 包，需在此独立环境里保持一个 `opencv-contrib-python` 安装。

## 2. 准备 ArUco

项目已附带 `aruco_id0.png`：OpenCV `DICT_4X4_50`, ID=0。图像外部包含白边。

把图片打印/显示出来，并用尺子测量 **黑色方形外边缘** 的实际宽度。
默认代码假定为 `0.06 m`（6cm）。如果实际是8cm，用 `--marker-size 0.08`。

**白色留边不算在标记边长内**；不得拉伸成非正方形。

## 3. 第一轮运行：不需要乐高模型

```bash
python aruco_lego_ar.py
```

把印好的 ArUco 对准摄像头，将显示一块红色的 2×4 乐高积木。
按 `q` 或 ESC 退出。若相机未打开，试 `--camera 1`。

> 首次演示脚本使用估计相机内参，可见位姿和缩放偏差，正式使用需要相机标定。

## 4. 换成自己的模型

LDraw 模型直接用 `--model ../chicken.ldr` 或上面的启动器；以下 Studio/Blender 转换仅用于希望改用 GLB 的情况。

BrickLink Studio: `File > Export As > Collada (.dae)`。

在 Blender 导入 `.dae`，检查 LEGO 形状、颜色、朝向和网格，然后导出 `glTF 2.0 (.glb)`，保存 `lego.glb` 到当前目录。

```bash
python aruco_lego_ar.py --model lego.glb --up-axis y
```

GLB 通常使用 Y-up。OBJ/STL 则按实际情况选择 `--up-axis z` 或 `--up-axis y`。示例：

```bash
python aruco_lego_ar.py --model lego.obj --up-axis z
```

模型加载后会**自动缩放并把最低点放在 ArUco 平面上**。默认目标水平尺寸 `0.09 m`，可用 `--fit-width 0.12` 调整。它不一定等于真实模型原始比例，而是适合预览的视觉比例。

## 5. 减少 AR 偏差：摄像头标定（建议）

另准备一张 **10 列 × 7 行黑白格（内角点 9×6）** 的平整棋盘格。
在不同距离、不同角度下拍摄，保证棋盘完整可见；通常收集 15–25 个不同视角（不要只在同一个位置拍摄）。

```bash
python calibrate_camera.py --camera 0 --square-size 0.018
```

`SPACE` 拍摄当前视角，`c` 标定并保存到 `calibration.npz`，`q` 退出。 `--square-size` 要对应你打印出的实际方格边长（米）。

然后：

```bash
python aruco_lego_ar.py --model lego.glb --up-axis y --calibration calibration.npz
```

标定时与运行时的摄像头分辨率、焦距/变焦设置保持一致。

## 6. 为什么模型位置会对齐

- ArUco 检测返回视频像素中的四个角点；
- 已知码的物理边长即可定义角点在标记局部坐标系下的 3D 位置；
- OpenCV `solvePnP` 计算标记到相机的旋转和平移；
- `diag(1,-1,-1,1)` 把 OpenCV 相机轴转换为 OpenGL 相机轴；
- Pyrender 用一致的相机内参、模型位姿进行渲染；
- 依据深度缓冲区把真实帧和虚拟像素叠加。

## 7. 常见问题

- `AttributeError: module 'cv2' has no attribute 'aruco'`：确保在干净环境安装 `opencv-contrib-python`。
- `ImportError`/`np.infty`：按 `requirements.txt` 建立 NumPy 1.x 兼容环境。
- `NoSuchDisplayException` / OpenGL context 错误：必须在带桌面/正确 OpenGL 的电脑上执行；Linux 无桌面环境可能需要 EGL，须在 `import pyrender` 前 `PYOPENGL_PLATFORM=egl`，且系统驱动支持。
- 模型侧躺或上下颠倒：改 `--up-axis` 或去 Blender 里修正导出坐标。
- 模型漂移/歪斜：标定摄像头、确保打印的是精确正方形、测准码边长、改善光照、避免晃动和镜头畸变。
- 被现实中的物体遮住时模型仍盖在最上层：当前是基础 AR 叠加，没有现实物体深度遮挡；之后可用 RGB-D 深度图融合。
- 这是示例项目，摄像头 / OpenGL 兼容性需在你的设备上实测。
