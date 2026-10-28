# Linux / A800 部署

Linux 与 Windows 使用相同代码。保留 Windows 的默认 SSH 着色方式；在 GPU 服务器上可切换为本机 UniTEX，无需 SSH 回连自己。

## 复用环境和权重

已有可运行环境时，无需复制权重或虚拟环境。将项目内以下目录链接到已有资源即可：

- `.venv-hunyuan`：具备 PyTorch CUDA、Hunyuan 及主流程图像处理依赖的 Python 3.11 环境。
- `Hunyuan3D-2`、`legolization`、`LDrawLibrary`、`weights`：对应的源码、零件库和模型缓存。
- `segmentation_lab/florence_env`、`florence_models`、`sam2_env`、`sam2_models`、`sam2_repo`：对应抠图环境、权重和源码。使用项目最新版抠图适配脚本。

链接的目标需要可读、环境解释器需要可执行。不要向队友的环境安装包。虚拟环境不可直接从 Windows 复制到 Linux。

如已有 LEGO 环境的依赖不匹配，可为新项目建立独立的小型兼容环境，在 `config.local.json` 中设置 `LEGO_PYTHON` 和 `LEGO_CLI` 的绝对路径；保留原环境。复用二进制包时必须保持相同的 Python 主次版本和平台。当前 Windows 基线使用 pyldraw3 Git 固定版本 `465f783723ebd4f0a234b826b22b32f2c1d18b73`（包版本 1.6.1）。

首次使用前核对 `upstream.lock.json`。资源存在并不代表版本兼容；先运行轻量测试和单独的生成验收。原有项目及未提交改动应保留。

## 本机 UniTEX

新建不会提交的 `config.local.json`：

```json
{
  "UNITEX_EXECUTION": "local",
  "UNITEX_REMOTE_ROOT": "/data/your-user/unitex-work",
  "UNITEX_GPU": 1
}
```

`UNITEX_REMOTE_ROOT` 保留旧配置名，在 `local` 模式下表示**本机资源目录**，其中应有：

```text
UniTEX/
envs/unitex/bin/python
models/FLUX.1-dev/
cache/huggingface/
```

此模式不调用 SSH/SCP。六视图、投影缓存和模型均写入当前运行目录的 `unitex/`。编辑六视图产生分支时，复制原缓存到新分支后再修改，原结果不受影响。旧任务没有本地相机缓存时会重建阶段一。

## 启动与访问

```bash
bash pipeline_ui.sh --port 7865
```

默认使用物理 GPU 1；可通过环境变量改变。启动脚本设置独立的临时目录、编译缓存和离线模型模式，并读取项目内 `.env`。UI 监听服务器回环地址，不直接暴露给外网。

在自己的电脑建立端口转发，保持该终端运行：

```bash
ssh -N -L 17865:127.0.0.1:7865 your-server
```

浏览器打开 `http://127.0.0.1:17865/`。建模、着色、校正和 LEGO 求解都在服务器运行；这个连接只用于访问页面。

助手需要在服务器 `.env` 中配置 `DASHSCOPE_API_KEY`，并执行 `chmod 600 .env`。不要把密钥提交到 Git 或直接写入启动命令。

`pyldraw3` 的包和生成颜色目录是两项独立资源。新账号没有原生颜色目录时，可用 LEGO 环境执行以下初始化；只读取现有 `LDConfig.ldr`，生成文件和配置全部放在项目 `.runtime/` 内，不改变调色板映射算法：

```bash
export XDG_CONFIG_HOME="$PWD/.runtime/config"
export XDG_DATA_HOME="$PWD/.runtime/data"
# 将下面解释器替换为实际的 LEGO_PYTHON。
.venv-lego/bin/python scripts/setup_lego_palette.py --ldraw LDrawLibrary/ldraw
```

启动脚本会设置同样的项目级 XDG 目录。私有配置、虚拟环境和运行缓存不能通过 UI 的文件下载接口访问。

Linux 的流水线与助手命令会分别创建进程会话；停止按钮向该任务进程组发送终止信号，包括其本机 GPU 子进程。

## 说明书

`instructions_workbench/.venv/bin/python` 使用独立的 Python 3.12 说明书环境，也可在依赖兼容时复用已有的 Legolization 环境。`config/local.json` 配置 Linux 的 LeoCAD 可执行文件、LDraw 路径，以及项目内部的输出目录。

无桌面的服务器需要 Xvfb 等虚拟显示环境。渲染器可以配置成一个包装脚本，在虚拟显示下调用 LeoCAD；不需要更改说明书规划逻辑或上游源码。下载兼容系统 GLIBC 的官方 Linux 构建。部署完成后必须实际生成 PNG 与 PDF 检查，不能仅凭命令成功退出判定就绪。

项目提供 `scripts/leocad_headless.sh`：将 [LeoCAD 官方 v23.03 Linux AppImage](https://github.com/leozide/leocad/releases/tag/v23.03) 解压到 `.runtime/leocad/squashfs-root/`，将兼容系统的 Xvfb 包解压到 `.runtime/xvfb/`。该组合已在 CentOS 8 上验证。执行 `chmod +x scripts/leocad_headless.sh`，并将其绝对路径设置为说明书配置的 `renderer`。包装脚本为每次渲染创建独立虚拟显示，使用 Mesa 软件渲染，结束后关闭显示，不占用固定端口或 GPU 显示服务。

## 验证和团队协作

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-hunyuan/bin/python -m unittest discover -p 'test_*.py'
```

测试之外，依次验证透明图输入、Hunyuan、UniTEX 两个阶段、方位校正、LEGO 小尺寸、卡片状态恢复和说明书。测试数据使用单独的运行目录，不覆盖旧模型。

共享运行环境不等于多人同时编辑同一个检出目录。建议通过 Git 分支合并功能，再部署一个统一 UI。复用权重的目录不要提前删除；所有仍在使用它的配置都切换并验收后再清理。
