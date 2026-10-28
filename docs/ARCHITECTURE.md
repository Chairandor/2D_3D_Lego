# 当前实现与实验边界

主流程由 Windows 端的 `pipeline_2d_to_lego.py` 编排。

1. **输入**：检查 Alpha 是否是有效透明底。符合条件直接复用；否则 Florence-2 自动检测主体框，SAM 2 对原框与扩展框各生成候选，`select_sam2_candidate.py` 选择结果。生成 `input/foreground.png`、检查图和报告。
2. **几何**：本地 NVIDIA GPU 运行 Hunyuan3D-2mini，输出 `mesh_shape_only.glb`。独立工具生成清理后的副本和几何检查图，原始结果保留。
3. **着色预览**：上传透明底图与无色网格到 Linux。UniTEX stage1 生成六视图；接入脚本保留既有 Alpha，并使用 NF4 FLUX 量化及 CPU offload。
4. **最终贴图**：批准六视图后运行 project。LTM 补全不可见面并生成 textured GLB；默认使用 Open3D ray backend。结果通过 SCP 下载到本地。
5. **方位与颜色**：依据支撑面、主轴、重心及原参考视图等线索进行通用 Y-up 校正；纹理转顶点色并映射为 LEGO 调色板。默认启用 `lego_colour_coherence.py` 的保守局部连贯处理，减少近似同色区域在调色板边界产生的零碎色块，输出 `oriented/lego_input.ply`。可用 `--colour-coherence off` 对照关闭；这一步不编辑已经生成的 MPD。
6. **积木**：显式批准后调用 Legolization。默认 `fast`、Y-up、表面体素化、单网格相位、不自动 hollow；保存 `lego-N/model.mpd`。Bundle 是另一个可选步骤。

## 推荐宽度

UI 在当前任务结束且 PLY 已存在时后台调用上游 `recommend_voxelization`。范围 8–128 Stud，`up=y`、`grid_phases=1`、`fill=False` 与当前构建一致。上游按轮廓误差、特征保留、连接度等排序；我们没有重写评分器。结果缓存到 `oriented/lego_recommendation.json`，网格时间戳或推荐范围改变则重新计算。点击“使用推荐值”只修改输入框。

## 断点

本地各阶段复用已有产物，`pipeline_state.json` 记录验收点。远端作业名由图片、网格内容与 seed/steps/projection 生成指纹。最终贴图依赖远端 camera_info 等缓存；远端缓存缺失时需要补跑 stage1。`--force` 会重跑，日常恢复通常无需使用。

UI 另用每个主运行目录的 `ui_current_state.json` 记录当前分支、卡片文件和选择、待确认候选及阶段进度。`ui_run_state.py` 将文件路径保存为相对路径。普通刷新/重新载入读取记录，仅在明确生成或选择修改版后更新；旧目录首次迁移按已经确认的候选来源匹配分支，不按时间挑选最新目录。记录损坏或引用文件丢失时报错，不静默换为初版。续跑会恢复所填目录自己的分支，避免从其他模型切回后覆盖指针。`ui_revisions.py` 对选择和指针进行同一文件锁内的读改写，防止并发保存互相覆盖。

## LEGO 详情与说明书

`web_assets/lego-detail.*` 在当前网页覆盖页中提供成品、说明书、候选比较。总零件统计通过 `ui_lego_details.py` 调用原生 BOM，零件缩略图共用一个 Three.js 离屏渲染器，按编号和颜色缓存并延迟加载。`ui_parts_pdf.py` 使用服务端当前模型的完整 BOM 生成单独图文清单，浏览器搜索筛选不影响导出的行数，导出前后校验源模型内容。

`ui_instructions.py` 在独立 Python 3.12 环境调用队友说明书工程的规划、LeoCAD 渲染、PDF/HTML 导出和累计限时静力分析。匹配结果使用源 MPD/LDR SHA256。图文完整后即可进入逐步阅读，静力状态随后更新；退出码 2 不等于图文生成失败。说明书页面只呈现具体步骤和本步 BOM，成品与总统计仍留在成品页。原始步骤 PNG 和 PDF 不因浏览器自动取景而改变。

## 历史路线

旧 SF3D、手工投影着色、Paint3D 与原生 Hunyuan Paint 试验保留便于比较，不自动用于主流程。`segmentation_lab/repair_cutout.py` 修边试验和 `--refine` 精修也没有默认开启。不要根据单张 PNG 的观感自动启用这些变换；它们可能改变后续重建的几何。

当前测试覆盖输入分流、验收顺序、朝向、部分颜色处理和 UI 编排，不代替多素材端到端验证。

## 阶段助手与候选版本

`ui_assistant.py` 向百炼对话服务提供受限工具选择，`ui_agent_jobs.py` 负责图片编辑或 Legolization CLI 的后台任务，`ui_revisions.py` 保存候选、确认状态与分支。输入图候选和六视图候选在确认后分别建立新分支；旧产物在后续阶段尚未生成时仍可查看。助手不能执行任意 shell 命令，也没有 3D 网格编辑器或 MPD 局部颜色编辑器。有关环境变量和现阶段限制，见 [部署说明](SETUP.md) 与 [阶段助手说明](../ASSISTANT_UI.md)。

六视图经 `ui_six_view.py` 固定最初六视图与原生 `mv_alpha.png`，后续编辑沿纹理分支继承同一蒙版。只合入完全不透明主体的内部 RGB，原始背景、Alpha、抗锯齿和边缘带保持像素一致；缺原始蒙版、快照校验失败或尺寸变化会拒绝编辑。此约束不能消除主体内部颜色/纹理幻觉，仍需比较候选。
