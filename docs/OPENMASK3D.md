# 场景分割 → LEGO

在工作台切换到「场景分割 → LEGO」，点击「载入场景探索」。本地工作台沿用 A800 上的 OpenMask3D 场景选单、文本查询、三维高亮和候选下载接口。先在下方设置 LEGO 宽度和源坐标参数，再点击候选旁的「用此目标生成 LEGO」，即可导入完整彩色点云并启动转换。也可以用「导入目标彩色 PLY」选择本地文件，再点击「生成彩色 LEGO」。有其他 3D 任务运行时仍可导入点云，待当前任务结束后即可点击生成。这个标签的转换结果存入 `runs/three_d/scene/`。

模型导出后即可预览和下载 MPD。装配说明有独立的处理状态。

本地前端基于服务器 OpenMask3D 项目的 `web_demo/static/`。后端仍通过原服务的 `/api/scenes`、`/api/scene`、`/api/query` 和实例接口读取数据。服务器服务由 OpenMask3D 项目内的 `manage_web_demo.sh` 管理，监听服务器的 `127.0.0.1:8765`。工作台默认通过本机 `http://127.0.0.1:18765/` 访问该服务，也可设置 `OPENMASK3D_VIEWER_URL`。服务关闭时可使用手动导入实例 PLY。单个实例文件上限为 128 MiB。使用 SSH 隧道时，请替换示例中的 SSH 用户名和主机地址。

```powershell
ssh -F none -o ExitOnForwardFailure=yes -N -L 18765:127.0.0.1:8765 user@openmask-host.example
```

`jackjack_dense` 可用 `a baby doll` 查找候选；按视觉预览选择对象。查询使用已计算的场景掩码和实例特征。[OpenMask3D 单场景结果格式](https://github.com/OpenMask3D/openmask3d#run-the-pipeline-on-a-single-scene-)

## 本地工具的场景清单

如需在脚本或 HTTP API 中直接检查原始掩码，可在工作台目录创建本机忽略的 `openmask_scenes.local.json`。每个条目填写同一次分割对应的原场景和保存结果的绝对路径。以下为路径示例，`scene`、`features` 和 `clip_weights` 请使用服务器的实际文件。

```json
{
  "jackjack_dense": {
    "label": "JackJack 场景",
    "scene": "/data/path/to/original/jackjack_dense.ply",
    "masks": "/data/OpenMask3D/output/custom_scenes/jackjack_dense/jackjack_dense_masks.pt",
    "features": "/data/path/to/jackjack_dense_openmask3d_features.npy",
    "clip_weights": "/data/path/to/the/same/CLIP/checkpoint.pt"
  }
}
```

浏览现有 Mask 只需 `scene` 和 `masks`。直接读取服务器产出的可信 `.pt` 时，服务端 Python 需要 PyTorch。语义检索另外使用 `features`、同款 CLIP 权重和服务端已安装的 `clip`。掩码也可用 `.npy` 或 `.npz`。数据页载入现有 mask，无需执行场景分割。可用 `OPENMASK3D_SCENE_CATALOG` 指定其他本机清单路径。

远端场景同时设置 `OPENMASK3D_SSH` 和 `OPENMASK3D_ROOT` 或 `OPENMASK3D_JOB_ROOT`。工作台读取远端原场景并在远端把可信 `.pt` 导出为校验过的便携数组，浏览器接收点云预览。打开服务器条目时会复制该条目的场景和便携掩码到本机工作台，原始拍摄序列、权重和模型环境留在服务器。读取已有场景时可省略 `OPENMASK3D_CHECKPOINT`。

## 连接已有的 GPU 环境

在本机忽略的 `config.local.json` 或环境变量中指定以下配置。`OPENMASK3D_PYTHON` 是服务器上已经装好 OpenMask3D 的独立 Python 可执行文件，`OPENMASK3D_CHECKPOINT` 是已有的 mask head 权重。

| 配置 | 含义 |
| --- | --- |
| `OPENMASK3D_ROOT` | 服务器上的 OpenMask3D 项目绝对路径 |
| `OPENMASK3D_PYTHON` | OpenMask3D 环境的 Python 绝对路径 |
| `OPENMASK3D_CHECKPOINT` | 实例掩码模型的 `.ckpt` 绝对路径 |
| `OPENMASK3D_SSH` | 已实现公钥登录的 SSH 别名。网页和 OpenMask3D 同机时省略 |
| `OPENMASK3D_SSH_KEY` | 可选，用户已有 SSH 私钥的本机路径 |
| `OPENMASK3D_JOB_ROOT` | 可选，服务器数据盘的任务输出目录，默认写入项目 `output/legolizer_ui/` |
| `OPENMASK3D_GPU` | 可选，指定该作业的 `CUDA_VISIBLE_DEVICES` |

每个远端任务用独立目录保留输入和掩码。配置 SSH 时先用自己的终端确认 `ssh -o ClearAllForwardings=yes -o BatchMode=yes 服务器别名 true` 能正常返回。工作台上传场景到配置的服务器，调用安装好的 OpenMask3D 掩码入口，只回传便携掩码数组。模型环境和权重留在服务器。 [OpenMask3D 单场景推理说明](https://github.com/OpenMask3D/openmask3d#run-the-pipeline-on-a-single-scene-)

## 使用已有分割结果

在服务器的 OpenMask3D Python 环境中执行以下命令，输入该项目本次运行可信的原始掩码文件。导出的 NPZ 包含 N×K 二值 `masks` 和原 PLY 的 `scene_sha256`，可与原场景 PLY 一起上传到网页。也支持相同格式的 NPY 掩码矩阵。

```bash
python openmask3d_worker.py 原场景.ply 导出掩码.npz --saved-mask 可信的服务器结果_masks.pt
```

本地展示示例使用既有的 Bunny、Tiko 和 JackJack 64 studs 试算布局。预览使用本地 LDraw Parts Library 的简化零件几何，导出的 `.ldr` 使用原零件编号。桌面稳定性和装配审计以该模型的求解报告为准。
