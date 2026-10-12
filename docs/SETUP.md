# 部署与复现

当前经过端到端使用的部署形态是 **Windows 本机 + Linux UniTEX GPU 服务器**。原实验机的 Hunyuan 环境继承了另一个 Conda 环境，因此不应直接拷贝虚拟环境。本仓库记录源码和关键依赖版本，但还没有在全新电脑上验证完整安装过程。下面是配置步骤，首次部署可能需要按显卡驱动与 CUDA 调整依赖。

## 1. 获取项目与上游源码

本机准备 Git、Windows OpenSSH（`ssh` / `scp`）、Python 3.11 和 uv。可把项目克隆到任意目录：

```powershell
git clone https://github.com/Chairandor/2D_3D_Lego.git
cd 2D_3D_Lego
python scripts/fetch_upstreams.py
```

脚本根据 `upstream.lock.json` 获取 Hunyuan、Legolization 与 SAM 2。默认不获取本机 UniTEX，因为实际推理在 Linux；需要查看源码时加 `--include-unitex`。已有目录只检查版本，不自动覆盖。

## 2. 配置本机环境

不同组件使用独立环境，防止 Transformers、NumPy 或 PyTorch 版本相互冲突。默认目录如下：

| 环境 | 默认路径 | 用途 |
| --- | --- | --- |
| Hunyuan / UI | `.venv-hunyuan` | UI、几何生成、清理、检查 |
| Legolization | `legolization/.venv` | 颜色转换、校正、尺寸推荐与积木构建 |
| Florence-2 | `segmentation_lab/florence_env` | 实底图主体框检测 |
| SAM 2 | `segmentation_lab/sam2_env` | 掩膜生成 |

### Hunyuan 与 UI

```powershell
uv venv --python 3.11 .venv-hunyuan
```

先在此环境安装与显卡驱动匹配的 CUDA 版 `torch` 和 `torchvision`。当前实验机的安装包元数据记录为 `torch 2.14.0+cu130` / `torchvision 0.29.0+cu130`；它们是环境记录，并非所有机器必须使用的版本。

再安装 Hunyuan 上游依赖以及本项目的几何检查依赖：

```powershell
uv pip install --python .venv-hunyuan/Scripts/python.exe -r Hunyuan3D-2/requirements.txt
uv pip install --python .venv-hunyuan/Scripts/python.exe "diffusers==0.33.1" "transformers==4.42.3" scipy Pillow opencv-python-headless
.\.venv-hunyuan\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

当前 runner 只做 shape-only 推理，不调用 Hunyuan 原生 Paint。首次生成会下载 `tencent/Hunyuan3D-2mini` 的 `hunyuan3d-dit-v2-mini/config.yaml` 和 `model.fp16.safetensors` 到项目 `weights/hf_hunyuan` 缓存。此步骤需要可访问 Hugging Face。

### Legolization

```powershell
cd legolization
uv sync --locked
cd ..
```

安装其上游定义的依赖；Python 版本由该项目决定。颜色转换和推荐脚本会调用这个环境，而不是 Hunyuan 环境。

LDraw 零件库单独准备到 `LDrawLibrary/ldraw`。若网络可用，可使用上游 `parts sync`；此前部署遇到过下载 403，因此必要时按 LDraw 的官方渠道下载并手工展开，确认该目录包含 `parts` 与颜色配置。

### Florence-2 与 SAM 2

透明底有效图片会跳过分割，因此可以先验证透明底路线，再部署这两套环境。

```powershell
uv venv --python 3.11 segmentation_lab/florence_env
uv venv --python 3.11 segmentation_lab/sam2_env
```

分别安装匹配的 `torch` / `torchvision` 后：

```powershell
uv pip install --python segmentation_lab/florence_env/Scripts/python.exe "transformers==4.42.3" timm einops Pillow huggingface-hub
uv pip install --python segmentation_lab/sam2_env/Scripts/python.exe -e segmentation_lab/sam2_repo
.\segmentation_lab\florence_env\Scripts\python.exe .\segmentation_lab\download_florence.py
```

SAM 2 使用 SAM 2.1 small checkpoint，按上游说明下载 `sam2.1_hiera_small.pt`，放到 `segmentation_lab/sam2_models/`。配置由 SAM 2 repo 提供：`configs/sam2.1/sam2.1_hiera_s.yaml`。当前 Florence loader 使用 `trust_remote_code=True` 加载官方模型代码，Windows 可选 flash-attn 的静态导入问题由外部 runner 处理。

## 3. 准备 Linux UniTEX

可以接入团队已经部署好的服务器，不需要每人重复下载权重。每位队友使用自己的 SSH 密钥，服务器需要接受其公钥。默认 SSH 端口为 22。

流水线要求远端 root 下的结构为：

```text
/data/unitex-work/
  UniTEX/                     上游源码
  envs/unitex/bin/python      配好的 Python 环境
  models/FLUX.1-dev/          FLUX 本地 diffusers 模型目录
  cache/huggingface/          Hugging Face 缓存
  pipeline-runs/             自动创建的任务目录
```

远端 root 可以修改，但内部相对路径目前固定。服务器需 NVIDIA GPU、匹配的 CUDA 工具链及足够内存；当前 runner 使用 NF4 量化与 CPU offload。我们没有给所有 GPU 测定最低显存保证。

先获取源码并固定版本：

```bash
mkdir -p /data/unitex-work
cd /data/unitex-work
git clone https://github.com/YixunLiang/UniTEX.git
git -C UniTEX checkout --detach affa1e29e665670dbdfd13ee4a3a68a45942df47
```

按这个版本的 UniTEX `env.sh` / README 创建环境，将 Python 放到 `envs/unitex/bin/python`。其 nvdiffrast、Kaolin、PyTorch 与 CUDA 必须互相兼容。当前外部 runner 另外使用 `bitsandbytes` 和 `open3d`；Open3D 替换光线查询后端的适配位于 `run_unitex_external.py`，上游源码无需改动。`unitex_tools/remote_*.sh` 是原部署诊断辅助脚本，运行前核对编译器路径和 GPU 架构参数。

### 权重准备

**流水线正式运行强制离线加载**，必须先在服务器下载缓存，避免生成一半才遇到 Hugging Face 网络错误。使用自己的账号接受所需模型许可，认证只保存在服务器本地。

1. 将 `black-forest-labs/FLUX.1-dev` 下载为上述 `models/FLUX.1-dev` 目录。
2. 在 `HF_HOME=/data/unitex-work/cache/huggingface` 下预缓存 `lyxun/UniTEX` 的 `mv_lora_weights.safetensors`、`delight_lora_weights.safetensors`、`ltm_model_weights.bin`。
3. 若 LTM 或渲染依赖会下载额外模型，也要先完成其初始化，确认在离线模式可以加载。

正式推理使用这些环境变量：`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`DIFFUSERS_OFFLINE=1`、`UNITEX_FLUX_MODEL=<root>/models/FLUX.1-dev`、`UNITEX_RAY_BACKEND=open3d`。不要把个人 Hugging Face token 或缓存上传到本仓库。

## 4. 配置并启动 UI

```powershell
Copy-Item config.example.json config.local.json
```

编辑本地配置：

```json
{
  "UNITEX_HOST": "user@your-gpu-host",
  "UNITEX_SSH_KEY": "C:/Users/YOUR_USER/.ssh/id_ed25519_unitex",
  "UNITEX_REMOTE_ROOT": "/data/unitex-work",
  "UNITEX_GPU": 1
}
```

GPU 编号指远端物理 GPU 编号；只有一张卡时应使用 0。UI 中的选择会覆盖此次任务的编号。环境变量优先于配置文件，CLI 的显式参数再覆盖默认值。`HUNYUAN_PYTHON` 可指定另一个 Hunyuan Python；默认使用项目 `.venv-hunyuan`。旧抠图路线可单独配置 `LEGACY_IMAGE_PYTHON`。

确认 SSH 可用后双击 `pipeline_ui.bat`，或：

```powershell
.\.venv-hunyuan\Scripts\python.exe .\pipeline_ui.py
```

打开 `http://127.0.0.1:7865/`，保持启动终端开启。UI 当前一个任务运行时拒绝启动另一个任务，避免把已有任务覆盖。

## 5. 初次验收建议

先用自己的简单透明底单主体图片，检查输入、无色几何、六视图、最终彩色模型、方位校正，再批准 LEGO。重复运行同一个输出目录时通常复用既有结果。尝试另一个 LEGO 尺寸只改 Stud 输入，无需重新生成几何或贴图。

仓库附有无需 SSH/GPU 的测试，但未声称本次打包已在新电脑验证端到端安装；完整推理仍应由部署者在自己的 GPU 和账号环境中完成一次验收。
