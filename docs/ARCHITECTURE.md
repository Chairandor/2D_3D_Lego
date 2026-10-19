# 当前实现与实验边界

主流程由 Windows 端的 `pipeline_2d_to_lego.py` 编排。

1. **输入**：检查 Alpha 是否是有效透明底。符合条件直接复用；否则 Florence-2 自动检测主体框，SAM 2 对原框与扩展框各生成候选，`select_sam2_candidate.py` 选择结果。生成 `input/foreground.png`、检查图和报告。
2. **几何**：本地 NVIDIA GPU 运行 Hunyuan3D-2mini，输出 `mesh_shape_only.glb`。独立工具生成清理后的副本和几何检查图，原始结果保留。
3. **着色预览**：上传透明底图与无色网格到 Linux。UniTEX stage1 生成六视图；接入脚本保留既有 Alpha，并使用 NF4 FLUX 量化及 CPU offload。
4. **最终贴图**：批准六视图后运行 project。LTM 补全不可见面并生成 textured GLB；默认使用 Open3D ray backend。结果通过 SCP 下载到本地。
5. **方位与颜色**：依据支撑面、主轴、重心及原参考视图等线索进行通用 Y-up 校正；纹理转顶点色并映射为 LEGO 调色板，输出 `oriented/lego_input.ply`。
6. **积木**：显式批准后调用 Legolization。默认 `fast`、Y-up、表面体素化、单网格相位、不自动 hollow；保存 `lego-N/model.mpd`。Bundle 是另一个可选步骤。

## 推荐宽度

UI 在当前任务结束且 PLY 已存在时后台调用上游 `recommend_voxelization`。范围 8–64 Stud，`up=y`、`grid_phases=1`、`fill=False` 与当前构建一致。上游按轮廓误差、特征保留、连接度等排序；我们没有重写评分器。结果缓存到 `oriented/lego_recommendation.json`，网格时间戳改变则重新计算。点击“使用推荐值”只修改输入框。

## 断点

本地各阶段复用已有产物，`pipeline_state.json` 记录验收点。远端作业名由图片、网格内容与 seed/steps/projection 生成指纹。最终贴图依赖远端 camera_info 等缓存；远端缓存缺失时需要补跑 stage1。`--force` 会重跑，日常恢复通常无需使用。

## 历史路线

旧 SF3D、手工投影着色、Paint3D 与原生 Hunyuan Paint 试验保留便于比较，不自动用于主流程。`segmentation_lab/repair_cutout.py` 修边试验和 `--refine` 精修也没有默认开启。不要根据单张 PNG 的观感自动启用这些变换；它们可能改变后续重建的几何。

当前测试覆盖输入分流、验收顺序、朝向、部分颜色处理和 UI 编排，不代替多素材端到端验证。
