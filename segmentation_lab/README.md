# 主体分割独立实验

> 以下是试验阶段的记录，不是当前主流程说明。现在默认使用 Florence-2 + SAM 2；
> 有效透明底输入直接通过。像素修边保持关闭，相关脚本仅用于对照试验。
> 部署见 [SETUP](../docs/SETUP.md)，主流程见 [README](../README.md)。

这里测试“由模型直接提取图片主体”的路线，不替换 `lego_preprocess.py`，也不改现有 SF3D、乐高环境和运行结果。

当前实验使用 `rembg==2.0.85` 的 `birefnet-general-lite`（CPU/ONNX）。虚拟环境在 `.venv/`，首次下载的模型在 `model_cache/`。模型与实验输出都保留在本目录；原图和旧的 mask 不会被修改。

在 PowerShell 中重新测试另一张图（请为每次实验指定一个全新的输出目录）：

```powershell
$env:REMBG_HOME = 'E:\3D_TEST\segmentation_lab\model_cache'
& 'E:\3D_TEST\segmentation_lab\.venv\Scripts\python.exe' 'E:\3D_TEST\segmentation_lab\compare_masks.py' 'E:\3D_TEST\models\chicken\7ken.png' --output-dir 'E:\3D_TEST\segmentation_lab\runs\new-test' --baseline-mask 'E:\3D_TEST\runs\7ken-clean-test\input\mask.png'
```

`--baseline-mask` 可省略。输出为 `foreground.png`、`mask.png`、`comparison.png` 和 `report.json`。对比图中绿色表示两个 mask 都保留，蓝色仅新模型保留，橙色仅旧方案保留。重合率不是准确率；还需要人工检查主体细部、投影和多主体图片。

`runs/7ken_birefnet_lite/assessment.md` 记录了本次人工复核结论：小鸡右后方与阴影相接的主体区域漏切，本次结果未通过质量检查，不能作为默认方案。

后续同图实验 `birefnet-general`、`bria-rmbg`、`sam`（中心点和多提示点）也均未通过人工检查。各次 `runs/<实验名>/assessment.md` 保留失败原因；这些模型都未接入默认流水线。

`repair_cutout.py` 是抠图后的独立自动修复试验，不改原图或原 mask。它清除极小独立残片，并在有未抠原图时尝试窄边缘 matting。虽然透明 PNG 更干净，固定模型/种子的 3D 对照出现了更大的错误凸起；只清除残片的消融试验也有同类问题。因此此步骤尚未接入默认流水线，不能仅凭 PNG 观感、网格封闭性或单视角轮廓分数自动启用。结果保存在 `runs/7ken_florence_large_repaired/`、`runs/7ken_florence_islands_only/` 以及项目根目录 `runs/cutout-repair-shape-ablation.png`。

为排除推理随机性，原始输入以相同模型、种子和参数重跑；原始与复跑的 GLB 文件 SHA-256 完全一致。因此上述几何差异确由输入图的微小变化触发。当前试验只证明这种像素级修边不适合作为默认步骤，并没有否定后续基于图像语义的自动素材修复路线。

要单独把新的 `foreground.png` 送入 SF3D，可用主流水线的 `--preprocess-mode off` 和新的 `--output-dir`，避免重新运行旧预处理或覆盖已有结果。当前实验只评估模型，尚未自动切换主流水线默认方案。
