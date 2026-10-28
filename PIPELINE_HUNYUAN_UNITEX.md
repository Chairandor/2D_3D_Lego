# 2D → Hunyuan → UniTEX → 方位校正 → LEGO

主入口是 `pipeline_2d_to_lego.bat`（或 `pipeline_2d_to_lego.py`）。流水线代码在
`C:\path\2D_3D_Lego` 外层；不会修改 Hunyuan3D-2、UniTEX 或 legolization 仓库。

## 本地网页界面

双击 `C:\path\2D_3D_Lego\pipeline_ui.bat` 会打开 `http://127.0.0.1:7865`。界面可以：

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
C:\path\2D_3D_Lego\pipeline_2d_to_lego.bat E:\path\object.png
```

确认 `input\review.png` 后，生成 Hunyuan 无色模型并停在几何验收：

```powershell
C:\path\2D_3D_Lego\pipeline_2d_to_lego.bat E:\path\object.png --accept-preprocess
```

确认无色模型后，生成 UniTEX 六视图预览：

```powershell
C:\path\2D_3D_Lego\pipeline_2d_to_lego.bat E:\path\object.png --accept-preprocess --accept-geometry
```

确认 `unitex\mv_rgb.png` 后，完成贴图和方位校正，但仍不构建积木：

```powershell
C:\path\2D_3D_Lego\pipeline_2d_to_lego.bat E:\path\object.png --accept-preprocess --accept-geometry --accept-unitex
```

确认 `oriented\mesh_upright.glb` 后，才允许构建积木：

```powershell
C:\path\2D_3D_Lego\pipeline_2d_to_lego.bat E:\path\object.png --accept-preprocess --accept-geometry --accept-unitex --build-lego
```

带有效 Alpha、存在透明背景、主体非空且至少三个角透明的图片会被视为标准输入，
不会加载 Florence-2 或 SAM 2，也不会再次修改 Alpha。普通实底图片进入自动抠图，
该路线总会要求第一次人工验收，因为目前只在有限素材上
验证过，不能把模型置信度当作任意图片的正确性。此前试验性的像素级“修毛边”默认
关闭：它让透明 PNG 看起来更干净，但在固定 seed 的 Hunyuan 对照中产生了更差的
几何凸起。`--force` 会另存一次新的分割尝试并重跑阶段，普通断点续跑不要使用它。

## 从现有结果恢复

每个 UI 模型的主运行目录都有 `ui_current_state.json`，记录当前采用的内部运行分支、各阶段卡片的确切文件与原版/修改版选择、待确认候选以及阶段进度。刷新、重新载入和服务重启直接使用这份记录；不会根据哪个子文件夹较新或已有文件来换回原版。旧目录首次载入会根据已保存的明确选择迁移一次。记录损坏或引用文件丢失时显示错误，保留记录，不静默采用初版。

在「输出目录」填写已有模型的主目录，点击旁边的「载入结果」即可切换展示，不启动流水线。运行到下一验收点也会先恢复该目录自己的当前分支，不依赖之前打开的是哪个模型。运行产生新产物或修改版被上传/选定时更新记录；普通浏览刷新不会改变选择。

```powershell
# 从无色模型接 UniTEX（仍需原参考图）
C:\path\2D_3D_Lego\pipeline_2d_to_lego.bat E:\path\object.png --from-shape E:\path\shape.glb --accept-preprocess --accept-geometry

# 从已经贴好图的模型开始做方位校正
C:\path\2D_3D_Lego\pipeline_2d_to_lego.bat --from-textured E:\path\textured_mesh.glb
```

UniTEX 默认连接 `user@your-gpu-host`，使用
`%USERPROFILE%\.ssh\id_ed25519_unitex`，远端根目录为
`/data/unitex-work`。三项都可以分别用 `--unitex-host`、
`--unitex-key`、`--unitex-remote-root` 覆盖。

## 网页拼搭说明书

「成品展示」右上角的「导出零件清单」可下载独立图文 PDF，每项包括真实零件缩略图、名称、LDraw 编号、颜色及数量。该文件只包含总零件清单，不含成品封面或拼搭步骤；搜索筛选不会影响导出的完整清单。首次导出会准备全部零件缩略图，后续复用同一模型内容对应的 PDF。先生成零件统计后即可导出，无需先生成拼搭说明书。

点击 LEGO 卡片名称，在覆盖页面选择「生成说明书」。已有完整说明书时直接进入分步阅读；没有时自动排队生成，生成期间可以返回其他页面，重新进入会恢复任务进度。图文生成完成后即可阅读，后续静力分析继续运行。左侧显示当前步骤图，右侧只列本步零件，右下方用「上一步 / 下一步」切换，也支持左右方向键；有补充视角时可在该步查看另一侧。顶部可下载 PDF。总零件统计和成品模型仍在「成品展示」中。

本机说明书工程位于 `instructions_workbench/`，使用自己的 `.venv`。按该工程 README 安装 Python 3.12 依赖，在 `config/local.json` 配置 LeoCAD、完整 LDraw 库和项目内的输出目录。本机输出在 `runs/lego-instructions/`。UI 通过自有适配器 `ui_instructions.py` 调用其原生规划、渲染和图文导出接口，不使用队友的历史模型排除名单或成品索引，也不修改随附 Legolization 源码。

说明书根据输入 MPD/LDR 的 SHA256 匹配，文件内容改变后不会套用旧版结果。同一模型的重复点击复用同一任务，不同模型的生成任务依次运行。步骤图、逐步 BOM 和 PDF 不齐全时不会当作已完成结果；页面会显示实际生成错误并允许重试。说明书生成与静力分析状态分开，已知步骤风险显示在对应步骤下方。
