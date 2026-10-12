# 早期部署笔记：2D → Hunyuan → UniTEX → 方位校正 → LEGO

> 本文件保留早期实验机的命令示例，固定盘符与输出目录描述可能已过时。
> 当前使用方式以 [README](README.md) 和 [部署说明](docs/SETUP.md) 为准。
> 公开版本使用 `config.local.json` 配置服务器，启动脚本已改为相对路径；
> 多尺寸 LEGO 输出在 `lego-N/` 中。

主入口是 `pipeline_2d_to_lego.bat`（或 `pipeline_2d_to_lego.py`）。流水线代码在
`E:\3D_TEST` 外层；不会修改 Hunyuan3D-2、UniTEX 或 legolization 仓库。

## 本地网页界面

双击 `E:\3D_TEST\pipeline_ui.bat` 会打开 `http://127.0.0.1:7865`。界面可以：

- 使用本地图片路径或上传一张图片；
- 勾选已经通过的抠图、几何和 UniTEX 验收阶段；
- 实时查看终端输出；
- 预览检查图并打开或下载 GLB、PLY、MPD；
- 显式批准最终 LEGO 构建；
- 复用断点结果或手动停止当前任务。

网页只监听本机地址，不会公开到局域网或互联网。关闭启动网页的终端窗口会停止界面，
但已经生成的流水线文件仍保留在 `runs` 下。

## 阶段与验收点

1. `input/`：有效透明底图片直接通过；普通实底图由 Florence-2 定位主体、SAM 2 生成掩膜。
2. `hunyuan/`：Hunyuan3D-2mini 只生成无色、封闭的几何模型。
3. `unitex/mv_rgb.png`：UniTEX 六视图着色预览。
4. `unitex/textured_mesh.glb`：用 LTM 补全不可见面并生成最终贴图模型。
5. `oriented/`：通用朝向校正和带顶点色的 `lego_input.ply`。
6. `lego/`：只有显式传入 `--build-lego` 才会生成积木。

每个耗时阶段会复用已有结果。远程 UniTEX 缓存按输入图片、网格、seed、steps
和投影方式生成指纹，不会把另一个模型的缓存误接进来。

## 推荐使用顺序

第一次运行生成抠图并停在输入验收：

```powershell
E:\3D_TEST\pipeline_2d_to_lego.bat E:\path\object.png
```

确认 `input\review.png` 后，生成 Hunyuan 无色模型并停在几何验收：

```powershell
E:\3D_TEST\pipeline_2d_to_lego.bat E:\path\object.png --accept-preprocess
```

确认无色模型后，生成 UniTEX 六视图预览：

```powershell
E:\3D_TEST\pipeline_2d_to_lego.bat E:\path\object.png --accept-preprocess --accept-geometry
```

确认 `unitex\mv_rgb.png` 后，完成贴图和方位校正，但仍不构建积木：

```powershell
E:\3D_TEST\pipeline_2d_to_lego.bat E:\path\object.png --accept-preprocess --accept-geometry --accept-unitex
```

确认 `oriented\mesh_upright.glb` 后，才允许构建积木：

```powershell
E:\3D_TEST\pipeline_2d_to_lego.bat E:\path\object.png --accept-preprocess --accept-geometry --accept-unitex --build-lego
```

带有效 Alpha、存在透明背景、主体非空且至少三个角透明的图片会被视为标准输入，
不会加载 Florence-2 或 SAM 2，也不会再次修改 Alpha。普通实底图片进入自动抠图，
该路线总会要求第一次人工验收，因为目前只在有限素材上
验证过，不能把模型置信度当作任意图片的正确性。此前试验性的像素级“修毛边”默认
关闭：它让透明 PNG 看起来更干净，但在固定 seed 的 Hunyuan 对照中产生了更差的
几何凸起。`--force` 会另存一次新的分割尝试并重跑阶段，普通断点续跑不要使用它。

## 从现有结果恢复

```powershell
# 从无色模型接 UniTEX（仍需原参考图）
E:\3D_TEST\pipeline_2d_to_lego.bat E:\path\object.png --from-shape E:\path\shape.glb --accept-preprocess --accept-geometry

# 从已经贴好图的模型开始做方位校正
E:\3D_TEST\pipeline_2d_to_lego.bat --from-textured E:\path\textured_mesh.glb
```

UniTEX 默认连接 `user@your-gpu-host`，使用
`%USERPROFILE%\.ssh\id_ed25519_unitex`，远端根目录为
`/data/unitex-work`。三项都可以分别用 `--unitex-host`、
`--unitex-key`、`--unitex-remote-root` 覆盖。
