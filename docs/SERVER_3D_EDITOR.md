# 服务器版 3D 模型编辑

右侧「阶段助手」增加了「修改 3D 模型」模式。先选择本次流水线生成的模型，或上传一个 GLB，再输入一句修改要求。服务端直接进入 Prox-E：先渲染模型、用视觉模型结合截图和文字制定编辑计划，再运行相应的编辑路线。用户不需要填写模型类别或选择 GPU。

编辑完成后，候选 GLB 会显示在对话框和产物列表中。点「采用最新结果」后，下一次编辑会以该结果为输入，LEGO 步骤会使用随编辑生成的 PLY；点「恢复原模型」可撤销采用。原始 GLB 始终保留。队友已有的 Qwen 聊天、输入图编辑、六视图编辑和 Windows 设置都保留。

## 服务器配置

在 `config.local.json` 中填写 UniTEX 和 Prox-E 在服务器上的路径。Prox-E 默认查找同级目录 `3D/third_party/Prox-E`，并在运行编辑时读取其 `scripts/proxe_env.sh` 和 `config/proxe_relay.env`。后者存放中转站密钥，只留在服务器上，不提交到 Git。Qwen 阶段助手另外读取 `DASHSCOPE_API_KEY`。

启动方式：

```bash
cd /data/data_zxn/2D_3D_Lego_server_integration
.venv-hunyuan/bin/python pipeline_ui.py --no-browser --port 7877
```

网页只监听服务器的 `127.0.0.1`。在 Mac 上用既有服务器 SSH 地址建立隧道，例如 `ssh -N -L 7877:127.0.0.1:7877 zxn@你的服务器地址`，然后打开 `http://127.0.0.1:7877/`。服务器上需预先提供 Hunyuan、Legolization、Prox-E 环境和 LDraw 零件库。Windows 本地配置仍见 [SETUP.md](SETUP.md)。
