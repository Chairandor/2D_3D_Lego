# 三维核心与 ArUco 源码部署

2026-11-06 将原 LEGOLIZER 的 `src/legolizer/`、`aruco_lego/` 源码纳入本仓库。保留 orientation、LDraw 几何读取、依赖声明与测试。UI 使用现有图片、3D、ArUco 三个标签。

## 路径

- `src/legolizer/`：毫米制三维处理、体素、颜色、orientation、legolization 接入及 CLI。
- `aruco_lego/`：LDraw 读取、ArUco 叠加、相机标定、标记图和独立窗口入口。
- `pyproject.toml` / `requirements-3d.txt`：三维核心 Python 3.10–3.12 依赖；后端和 AR 使用各自独立环境。

无显式 `LEGOLIZER_ROOT` 时，先复用已有父目录 `src/legolizer/`，否则使用本仓库内的源码，以兼容原开发机布局。`config.example.json` 使用 `"LEGOLIZER_ROOT": "."`，明确选择本仓库；相对路径按仓库根目录解析。已有 `config.local.json` 不会被改写，环境变量优先。

## 三维环境

在仓库根目录，用已有的 Python 3.10–3.12 创建环境并安装（以下以 Windows Python 3.11 为例）：

```powershell
py -3.11 -m venv .venv-3d
.\.venv-3d\Scripts\python.exe -m pip install -e .
.\.venv-3d\Scripts\python.exe -m legolizer --help
```

将以下字段合入本机 `config.local.json`，保留已有的图片流水线配置：

```json
{
  "LEGOLIZER_ROOT": ".",
  "PROCESSING3D_PYTHON": "C:/path/to/2D_3D_Lego/.venv-3d/Scripts/python.exe",
  "ARUCO_ROOT": "C:/path/to/2D_3D_Lego/aruco_lego",
  "ARUCO_PYTHON": "C:/path/to/2D_3D_Lego/aruco_lego/.venv/Scripts/python.exe",
  "LDRAW_LIBRARY": "C:/path/to/ldraw"
}
```

`PROCESSING3D_PYTHON` 应指向三维环境，不能使用缺少依赖的系统 Python 3.14。Linux 将 `Scripts/python.exe` 改为 `bin/python`。本页的安装命令由使用者主动执行；此次源码上传没有安装或下载依赖。

LEGO 生成仍使用独立的 legolization 环境。按 [部署说明](SETUP.md) 和 `upstream.lock.json` 准备 `legolization/`；兼容原核心根目录的 `research/opensource/legolization/` 布局。第三方源码和权重没有复制进本次新增目录。

原 Bunny / Rhino / Tiko 数据、根目录 LDR 模型和运行结果不包含在此次源码上传中。可以在网页上传自己的 STL / 点云 PLY 或 LDR / MPD；缺少默认示例文件时需选自己的输入。上传后明确确认源单位和上轴；LEGO 使用毫米、Z-up、8×8×9.6 mm 体素及 table 条件。

## ArUco 环境

AR 与三维核心保持环境隔离：

```powershell
py -3.11 -m venv aruco_lego/.venv
.\aruco_lego\.venv\Scripts\python.exe -m pip install -r aruco_lego/requirements-lock.txt
.\aruco_lego\.venv\Scripts\python.exe -m unittest discover -s aruco_lego -p 'test_*.py' -v
.\start_aruco_ui.ps1
```

也可直接运行 `aruco_lego/aruco_lego_ar.py` 显示程序生成的红砖。真实 LDraw 模型需要本机完整零件库，Studio 专用色可显式配置 `ARUCO_STUDIO_COLORS`。源码中附带的 `aruco_id0.png` 使用 `DICT_4X4_50`、ID 0；实测黑色正方形边长，不计白边。

运行数据、相机画面、环境、标定结果和零件库被 Git 忽略。相机识别与叠加不等于精确标定、真实比例或现实遮挡。历史开发机验收见 [ArUco README](../aruco_lego/README.md)；此次上传未重新启动摄像头。

## 验证

```powershell
.\.venv-3d\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-3d\Scripts\python.exe -m unittest test_core_paths test_ui_3d_jobs test_ui_aruco -v
```

测试中的模拟后端验证接入契约，不能替代真实求解。buildable、stable、instructions_status 和 instruction_audit_valid 分开解释；partial 不能作为完整说明书认证。
