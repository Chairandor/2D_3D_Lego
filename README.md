# 2D_3D_Lego

把单张主体图片转换为带颜色的 3D 模型，再转换成 LEGO 模型的实验流水线。

目前的主路线：

```text
图片 → 透明底检查 / Florence-2 + SAM 2 抠图
     → Hunyuan3D-2mini 无色几何
     → UniTEX 六视图着色 + LTM 贴图
     → 自动朝向校正 + LEGO 调色板映射及局部颜色连贯处理
     → Legolization → MPD / 可选 Bundle
```

本仓库用于队友交流、查看实现和部署复现。包含我们编写的接入代码、网页 UI、分析工具和历史试验；模型权重、虚拟环境、LDraw 零件库、用户上传图片、历史运行结果和私人配置不在仓库中。这是源码版，首次使用需要配置本地环境与远端 UniTEX，尚未提供一键安装包。

## 当前能力

- 有效透明底输入跳过重新抠图；实底图使用 Florence-2 定位与 SAM 2 候选掩膜。
- Hunyuan 生成无色几何，UniTEX 负责六视图生成和最终贴图；六视图完成并不代表贴图完成。
- UniTEX 在 Linux GPU 服务器运行，最终模型回传本地，方位校正和积木构建在本机执行。
- 有输入、几何、六视图和 LEGO 构建的验收开关，支持断点续跑。
- UI 展示图像与模型下载链接、停止任务、求解警告及 Legolization 原生推荐宽度；GLB、PLY 模型可在卡片中自动旋转、拖动查看，LEGO MPD 预览还需要本机 LDraw 零件库。
- 可选的右侧阶段助手通过百炼 API 对话；可对输入透明底图、UniTEX 六视图生成图片候选，并调用一部分 Legolization CLI 工作流。候选须在卡片上确认，原版和旧阶段产物不会立即覆盖。
- 同一模型可生成多个 Stud 尺寸，结果分别保存在 `lego-8/`、`lego-16/` 等目录。

当前主流水线 UI 一次运行一个任务，阶段助手也一次只执行一个后台操作。推荐宽度是在 8–128 Stud 范围内按体素化质量选择的参考值，不等于积木稳定性认证。单图看不到的背面细节由模型推测，少量素材的良好结果不能代表所有图片都能同样成功。阶段助手的 3D 几何与朝向编辑尚未接入；LEGO 阶段也尚不能按自然语言精确修改局部颜色或零件结构。

3D 预览使用仓库内置的 Three.js 文件，无需访问外部 CDN；浏览器需支持 WebGL。`git clone` 后不会自动获得 LDraw 零件库。LEGO MPD 网页预览需要将完整零件库放在项目根目录的 `LDrawLibrary/ldraw/`，其中应有 `parts/`、`p/` 和 `LDConfig.ldr`。仅运行 Legolization 的 `parts sync` **不会自动放到这个路径**；按 [部署说明](docs/SETUP.md) 再复制或手工解压到位。缺库时普通 MPD 构建及下载链接仍可使用，但网页不会显示 LEGO 旋转预览。

## 开始使用

1. 按 [部署说明](docs/SETUP.md) 准备 Windows 本机和 Linux GPU 服务器。
2. 将 `config.example.json` 复制为 `config.local.json`，填写自己的 SSH 地址、密钥路径和远端目录。若要使用阶段助手，另行设置 `DASHSCOPE_API_KEY` 环境变量；不设置也能运行主流水线。
3. 双击 `pipeline_ui.bat`，打开 `http://127.0.0.1:7865/`。
4. 上传图片，逐阶段检查产物后勾选验收项继续。相同输出目录会复用已有结果；一般不要勾选强制重跑。

界面只监听本机。要分享结果，发送生成的 GLB 或 MPD；要讨论代码，使用 GitHub Issues、分支或 Pull Requests。

命令行也可使用：

```powershell
.\pipeline_2d_to_lego.bat C:\images\object.png
.\pipeline_2d_to_lego.bat C:\images\object.png --accept-preprocess --accept-geometry --accept-unitex
.\pipeline_2d_to_lego.bat C:\images\object.png --accept-preprocess --accept-geometry --accept-unitex --build-lego --target-studs 16
```

首次命令会停在验收点。没有显式传入 `--build-lego` 时不会构建积木。

## 代码导航

| 文件 / 目录 | 用途 |
| --- | --- |
| `pipeline_2d_to_lego.py` | 全流程编排、SSH 接入、朝向校正、颜色转换 |
| `pipeline_ui.py` | 本地网页 UI、任务状态、产物列表、停止和警告 |
| `ui_assistant.py` / `ui_agent_jobs.py` / `ui_revisions.py` | 阶段助手、后台操作、候选版本与分支状态 |
| `lego_colour_coherence.py` | 转入 Legolization 前，减轻顶点调色板映射产生的局部杂色；不修改 MPD |
| `run_hunyuan_shape_trial.py` | Hunyuan3D-2mini shape-only 推理 |
| `clean_shape_trial.py` / `shape_component_cleanup.py` | 保留原网格并清理极小独立碎片 |
| `shape_qa.py` | 几何与输入轮廓的检查工具 |
| `segmentation_lab/` | 主流程抠图脚本与分割、修边试验 |
| `unitex_tools/run_unitex_external.py` | UniTEX 分阶段接入、FLUX 量化、Open3D ray backend |
| `lego_scale_recommendation.py` | 调用 Legolization 原生尺寸推荐器 |
| `lego_preprocess.py` / `lego_refinement.py` | 旧路线及可选精修试验，默认主流程不启用所有功能 |
| `test_*.py` | 不需要 GPU 或 SSH 的本地测试 |
| `upstream.lock.json` | 实际使用过的上游源码版本 |

SF3D、Paint3D、Hunyuan 原生着色等脚本保留作历史研究材料，不是当前默认主流程。部分历史工具仍保留示例路径，使用前需按自己的目录调整。抠图后的像素修边曾使 3D 几何变差，因此目前默认不启用，详见 [架构说明](docs/ARCHITECTURE.md) 与 [阶段助手说明](ASSISTANT_UI.md)。

## 测试与协作

只阅读和修改编排代码不需要下载大模型。可以用 Python 3.11 的轻量环境运行测试：

```powershell
python -m venv .venv-review
.\.venv-review\Scripts\python.exe -m pip install -r requirements-review.txt
.\.venv-review\Scripts\python.exe -m unittest discover -p "test_*.py"
```

请把环境地址放在 `config.local.json` 或环境变量里，避免提交私人配置。GitHub Actions 会运行轻量测试；它不验证 GPU 推理、SSH 和真实积木求解。建议每个功能单独建分支，记录输入类型、参数、运行阶段和问题现象。

## 上游来源

- [Hunyuan3D-2](https://github.com/Tencent-Hunyuan/Hunyuan3D-2)，几何权重为 [Hunyuan3D-2mini](https://huggingface.co/tencent/Hunyuan3D-2mini)。
- [UniTEX](https://github.com/YixunLiang/UniTEX)，相关权重为 [lyxun/UniTEX](https://huggingface.co/lyxun/UniTEX) 和 [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev)。
- [Legolization](https://github.com/hbmartin/legolization)。
- [SAM 2](https://github.com/facebookresearch/sam2) 与 [Florence-2-large-ft](https://huggingface.co/microsoft/Florence-2-large-ft)。

接入脚本在上游仓库外运行，部分适配通过运行时替换实现，不修改上游磁盘源码。上游源码和权重按各自项目条款获取；本仓库没有把它们重新打包或授予统一许可，见 [来源与许可说明](THIRD_PARTY.md)。

## 服务器上的 3D 模型编辑

可从输入区加载队友已有产物，直接查看中间图片、3D 模型和 LEGO 结果。点模型卡片上的“编辑这个模型”，在右侧助手输入文字后由 Prox-E 生成修改版；修改版与原版在同一卡片比较并选择。采用修改版后，LEGO 步骤读取该候选的 PLY。部署与配置见 [服务器 3D 编辑说明](docs/SERVER_3D_EDITOR.md)。
