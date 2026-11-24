# 服务器版 3D 模型编辑

网页中的六步是：输入 → Hunyuan 几何 → UniTEX 六视图 → 贴图与校正 → 编辑 3D 模型 → LEGO。

## 使用已有产物

输入区的「直接加载队友已有结果」会列出 `/data/data_hyt/2D_3D_Lego/runs` 中已有贴图 GLB 的运行。选择后，页面会复制需要的图片、GLB、PLY 和已有 MPD 到本项目 `runs/`，然后直接展示中间产物。可以马上编辑 3D 模型，也可以用现有贴图结果进入 LEGO，无需重跑 Hunyuan 和 UniTEX。如目录不同，可在 `config.local.json` 设置 `EXAMPLE_RUNS_ROOT`。

上传 Mac 图片时，请用「上传图片」按钮；网页会显示预览并自动填入**服务器上的**路径。Mac 本地路径不能直接填入服务器的图片路径框。新图片从头运行的 Hunyuan 和 UniTEX 阶段可能比较慢，页面默认按验收点逐步运行。

## 卡片与对话

队友原有的图片编辑方式是：在右侧阶段助手选「输入图片与抠图」或「六视图与着色」，用文字要求 Qwen 生成修改版，然后在中间产物卡片中选择原版或修改版。这不是直接在图像上框选区域。

点击彩色模型卡片的名字，打开大预览和 3D 修改对话。Prox-E 生成可用 GLB 后即可预览或继续编辑。点击「检查」会显示网格和保色结果。点击「保存修改成果」给当前版本命名，另存后会新增一张卡片。关闭编辑窗口会丢弃未保存草稿。生成 LEGO 时可以选择任意已保存的彩色模型，同宽度的不同模型拥有独立的构建结果。

## 服务器配置

在 `config.local.json` 中填写 UniTEX 和 Prox-E 在服务器上的路径。Prox-E 默认查找同级目录 `3D/third_party/Prox-E`，并在运行编辑时读取其 `scripts/proxe_env.sh` 和 `config/proxe_relay.env`。后者存放中转站密钥，只留在服务器上，不提交到 Git。Qwen 阶段助手另需在 `config.local.json` 的 `DASHSCOPE_API_KEY` 填写百炼密钥，填写后重启 UI。用户不需要填写 Prox-E 模型类别或选择 GPU；GPU 由服务器的 `PROXE_GPU` 决定。

Linux 说明书可复用服务器上已有的 Python 3.12 环境、LeoCAD 和 Xvfb。把绝对路径分别填入私有 `config.local.json` 的 `INSTRUCTIONS_PYTHON`、`INSTRUCTIONS_LEOCAD_BIN`、`INSTRUCTIONS_XVFB_BIN`；说明书的私有 `instructions_workbench/config/local.json` 把 `renderer` 指向本项目 `scripts/leocad_external.py`，`ldraw_dir` 指向共享 LDraw，`output_root` 指向本项目 `runs/lego-instructions`。这样渲染临时文件和结果只写入本项目，无需复制共享二进制、权重或零件库。

启动方式：

```bash
cd /data/data_zxn/2D_3D_Lego_server_integration
.venv-hunyuan/bin/python pipeline_ui.py --no-browser --port 7877
```

网页只监听服务器的 `127.0.0.1`。在 Mac 上用既有服务器 SSH 地址建立隧道，例如 `ssh -N -L 17877:127.0.0.1:7877 zxn@你的服务器地址`，然后打开 `http://127.0.0.1:17877/`。服务器上需预先提供 Hunyuan、Legolization、Prox-E 环境和 LDraw 零件库。Windows 本地配置仍见 [SETUP.md](SETUP.md)。

## Windows 工作台连接 A800

在运行 Prox-E 的服务器和 Windows UI 各自的私有 `config.local.json` 中设置相同的 `MODEL_EDIT_BRIDGE_TOKEN`。使用随机生成的至少 32 个字符的令牌，并通过私有渠道配置。Windows 端再设置 `MODEL_EDIT_BRIDGE_URL` 为本机 SSH 隧道端口，例如 `http://127.0.0.1:17877`。隧道转发到服务器上的 UI 端口。服务器端只配置令牌。配置成功后重启相应的 UI。

Windows UI 将选中的 GLB 传到服务器临时会话，计算完成后将候选 GLB 下载回本机预览。「保存修改成果」把 GLB 另存到本机的成果目录，LEGO 继续使用本机流水线。视觉模型和模型服务凭据在服务器端的 Prox-E 配置里维护。
