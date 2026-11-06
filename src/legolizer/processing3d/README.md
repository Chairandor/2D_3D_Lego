# 三维处理

本目录统一保存原 `geometry/`、`reconstruction/`、点云/体素数据结构、预览及颜色处理。主入口仍为项目根目录的 CLI 或 Web 工作台。

- `conversion.py`：Mesh 采样、Poisson/Ball Pivoting 点云重建。
- `mesh.py`：读写、清理、尺度、轴向转换、网格质量统计。
- `voxel.py`、`volume.py`：体素化、Marching Cubes、占用数组/颜色与 NPZ。
- `rgbd.py`、`ycb.py`、`datasets.py`：RGB-D 重建、YCB 标定适配、显式 Redwood 下载。
- `colors.py`：RGB 到常用 LDraw 色号量化。
- `preview.py`：Mesh、点云、体素的信息和显式可视化。
- `orientation.py`：未知来源 ±X/±Y/±Z 几何判断，证据不足时拒判。
- `source_frame.py`：主流程显式 SF3D 来源预设，输出 Z-up。
- `source_orientation.py`：保留的独立 SF3D 几何主轴/站姿算法，输出 Y-up；原交接包另外保留作对照。

普通转换明确指定源单位倍率，输出毫米，保留源上轴；LEGO 主流水线再转换到毫米制右手系 Z-up，保存完整矩阵。Voxel 保存原点、体素尺寸、语义和颜色。RGB-D 输入必须包含图片、内参与位姿，不能用单个点云冒充 TSDF 输入。

旧 `getPoint.py`、`pointVectorize.py` 已删除，分别使用：

```powershell
python -m legolizer mesh-to-pointcloud <mesh> <points_mm.ply> --source-unit-mm <倍率>
python -m legolizer pointcloud-to-mesh <points_mm.ply> <mesh_mm.ply> --poisson-depth 8 --density-quantile 0.005
```

安装与用法见根目录的 `docs/SETUP_3D_AR.md`。原始数据集不随源码分发；ArUco 源码位于 `aruco_lego/`。
