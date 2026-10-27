# 乐高中文说明书：先说明书，后稳定性

真实 `.ldr` / `.mpd` → 严格导入 → **无静力求解规划** → BOM、LeoCAD 图片、中文 PDF/HTML → **显示可打开路径** → 自动稳定性分析（每模型累计最多 30 分钟）。未通过或超时仍保留草稿。不会启动三维重建、网格积木化或 StableLego/Gurobi 实验。

## 安装、配置与 VS Code

前置条件：Windows、Python **3.12** 64 位及 Python Launcher、LeoCAD、完整 LDraw 库（根目录包含 `LDConfig.ldr`、`parts` 和 `p`）。固定使用随附的 legolization **0.6.0**，不依赖原电脑的虚拟环境。

克隆仓库的 `instructions` 分支后，从仓库根目录执行：

```powershell
Set-Location .\workbench
powershell -NoProfile -ExecutionPolicy Bypass -File .\Setup.ps1
Copy-Item .\config\portable.example.json .\config\local.json
notepad .\config\local.json
code .\Lego_Workbench.code-workspace
```

只需改 `renderer` 为自己的 `LeoCAD.exe` 路径、`ldraw_dir` 为自己的 LDraw 根目录；可将 `output_root` 改为足够容量的输出目录。相对路径以工程目录为基准。已有配置请保留，不要用模板覆盖。模板如下：

```json
{
  "renderer": "E:/Software/LeoCAD/LeoCAD.exe",
  "ldraw_dir": "E:/Software/LDraw/ldraw",
  "output_root": "outputs",
  "reuse_roots": [],
  "ground": true,
  "step_size": 7,
  "stability_policy": "warn",
  "models": {}
}
```

安装只写工程 `.venv`；pip 缓存、安装临时文件也在工程磁盘。不改其他环境。直接依赖固定在 `requirements.txt`，完整已测试版本在 `requirements-lock.txt`（不含本机路径）；legolization 另从 `vendor/legolization060` 安装。首次安装需要网络。Python、LeoCAD、LDraw 不随包提供。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Run.ps1 -Mode check
```

## 单模型和批量命令

在 `workbench` 目录执行，用自己的输入替换示例路径：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Run.ps1 -InputModel "E:\Models\new_model.mpd" -Title "新模型"
```

同样支持 `.ldr`。原有 STEP 按源顺序及子模型边界保留，较大步骤按 `step_size` 拆成连续小组。无 STEP 时按层高、y、x、实例 ID 自下而上排序；不调用静力或插入求解。需要人工/后续分析确认拼搭可靠性，**不会伪造稳定性通过**。

输入必须有真实积木布局，引用的子模型保持相对目录。Git LFS 指针、缺失引用、未知零件、碰撞和不支持的变换会保存诊断并停止该模型，不丢件、不吸附、不修改原模型。不会将 PLY/照片改扩展名冒充输入。已有完整计划可用 `-PlanRun "E:\SavedPlan"` 指定，必须通过来源、几何和覆盖核验。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Batch.ps1
```

默认批量清单包含九个本次成品，核对哈希后直接复用。新批次另存 JSON 清单，用 `-Manifest` 指定；每项需有 `id`、`name`、`input`（相对工程或绝对路径），可加 `sha256`。新模型不要设 `reuse_only`。输入按哈希去重，依次运行、逐项保存，不并行启动大量求解器。狐狸 8 凸点版仅复用，缺少可核验成品就明确报错，不能重新生成。

默认入口必须指定输入，不会运行 `-Model all`；`-Mode full` 也走新流程。历史兼容模式只供维护，日常使用以上命令。

## 输出、累计限时与中断恢复

先检查引用、逐件覆盖、BOM、连续步骤及非空步骤；明确的子组件装入页允许不取新零件。步骤 MPD 与原模型展开后，逐件比较零件、颜色、精确位置及变换。

PDF/HTML 和图片生成并检查后，终端立即打印 **“说明书已生成，稳定性待分析”** 及两个完整路径，文件已经存在且可打开。随后无需确认，自动分析最终模型和全部步骤的自重静力。

整个模型共享 **1800 秒累计墙钟预算**，包括分析子进程启动、导入、最终模型和所有步骤；每次 LP 不重新计时。超时只终止本次拥有的子进程及其子进程，不影响其他模型/无关进程。已完成的每项分析分别原子保存，超时或异常不自动重试。

完成/超时后更新报告和说明书状态文字，复用原图，不重新规划或渲染。PDF 更新先写临时文件；若查看器锁住原 PDF，保留它并打印旁边的 `instructions_cn_status.pdf` 更新版路径。稳定性未通过不会撤回说明书。

输出根默认为 `workbench/outputs`，各模型独立保存 `instructions_first_<输入哈希>_<规划哈希>/`：计划、BOM、步骤 MPD、`stability_summary.json`、`stability_report.html`、`workflow_result.json`、`Result.zip` 和 `booklet_detail_...` 中的 PDF/HTML/图片。最近一次结果见 `LATEST_WORKFLOW.json`，批量进度见 `batch_instructions_first.json`，错误诊断在 `errors/`。

| 独立字段 | 状态 | 含义 |
|---|---|---|
| 说明书生成 | PASSED | 覆盖、步骤图、HTML 引用、PDF 可读检查通过；新模型仍建议人工审阅排版 |
| 稳定性 | PENDING / 待分析 | 尚无完整结论 |
| 稳定性 | PASSED / 通过 | 最终模型、全部步骤自重静力及最终连通性检查通过 |
| 稳定性 | FAILED / 未通过 | 完成分析，发现静力或连通性风险 |
| 稳定性 | TIMEOUT / 超时未完成 | 总预算耗尽，保留部分结果；未检查步骤仍未验证 |
| 稳定性 | ERROR / 求解异常 | 求解或进程异常，不能推断结构通过 |

说明书生成成功不是稳定性认证。自重静力通过也不代表按压插入或实物认证；已有插入风险继续保留，新计划不推定插入通过。本次没有稳定性优化。`ground=true` 只在分析布局中选择地面基准，不改交付 MPD；报告记录参数及地面拉力假设，不能把底板假设等同于普通桌面。目标值不是倒塌概率。StableLego/Gurobi 历史记录独立保留，未重跑。

预算在 `analysis_budget/<输入SHA256>/budget.json`；不要删除/搬迁预算重新计时。重复命令核验哈希后复用完整结果，不自动重试终结的分析。正常中断会停止拥有的子进程并保存结果；监督进程异常中断则保守记为异常/超时，不启动重复任务，请按预算记录检查 PID。未完成图文使用原输入及输出目录执行同一命令恢复。详情见 [执行流程与状态](docs/执行流程与状态.md)。

## 已有成品（全部原样复用）

| 模型/规格 | 件数 | 步数 | PDF 页数 | 图文状态 | 稳定性状态 | PDF / HTML |
|---|---:|---:|---:|---|---|---|
| 狐狸 8 凸点 | 65 | 12 | 24 | 通过、已验收 | 未通过，风险保留 | [PDF](../instructions/Fox_08stud/instructions_cn.pdf) / [HTML](../instructions/Fox_08stud/instructions_cn.html) |
| 橙子 8 凸点 | 205 | 51 | 67 | 通过 | 未通过，中间步骤风险 | [PDF](../instructions/Orange_08stud/instructions_cn.pdf) / [HTML](../instructions/Orange_08stud/instructions_cn.html) |
| 苹果 8 凸点 | 286 | 73 | 89 | 通过 | 未通过，中间步骤风险 | [PDF](../instructions/Apple_08stud/instructions_cn.pdf) / [HTML](../instructions/Apple_08stud/instructions_cn.html) |
| 狐狸 18 凸点 | 336 | 50 | 66 | 通过、草稿 | 超时未完成；最终未通过 | [PDF](../instructions/Fox_18stud/instructions_cn.pdf) / [HTML](../instructions/Fox_18stud/instructions_cn.html) |
| 狐狸 24 凸点 | 483 | 71 | 88 | 通过 | 未通过，风险保留 | [PDF](../instructions/Fox_24stud/instructions_cn.pdf) / [HTML](../instructions/Fox_24stud/instructions_cn.html) |
| 橙子 14 凸点 | 587 | 105 | 110 | 通过、源 STEP 草稿 | 超时未完成；最终通过 | [PDF](../instructions/Orange_14stud/instructions_cn.pdf) / [HTML](../instructions/Orange_14stud/instructions_cn.html) |
| 狐狸 27 凸点 | 615 | 96 | 113 | 通过、草稿 | 超时未完成；最终未通过 | [PDF](../instructions/Fox_27stud/instructions_cn.pdf) / [HTML](../instructions/Fox_27stud/instructions_cn.html) |
| 狐狸 30 凸点 | 697 | 103 | 121 | 通过、草稿 | 超时未完成；最终未通过 | [PDF](../instructions/Fox_30stud/instructions_cn.pdf) / [HTML](../instructions/Fox_30stud/instructions_cn.html) |
| 狐狸 32 凸点 | 796 | 121 | 139 | 通过、草稿 | 超时未完成；最终未通过 | [PDF](../instructions/Fox_32stud/instructions_cn.pdf) / [HTML](../instructions/Fox_32stud/instructions_cn.html) |
| 小鸡（指定历史 PDF） | 273 | 59 | 63 | 原样收录 | 历史记录单列，未重算 | [PDF](../instructions/Chicken_legacy/Chicken_Instructions_CN.pdf) |
| 兔子（指定历史 PDF） | 820 | 121 | 138 | 原样收录 | 历史最终未通过，未重算 | [PDF](../instructions/Bunny_legacy/Bunny_instructions_cn.pdf) |

完整输入哈希、已知风险与核验见 [成品清单](../instructions/manifest.json) 和 [说明书索引](../instructions/README.md)。五个超时模型的逐步骤静力仍未完成；本次复用其草稿，不启动旧模型的新 LP。

排除 `7ken` 及原 `model(1).mpd`（SHA256 `dbf917b222136797636177c8ce1a104581e050f962f94c6b04ccc1b33790d056`）。`Fox/lego/model.mpd` 与狐狸 8 凸点版重复，按哈希去重。其他新 `.mpd` 或同名 `model.mpd` 正常支持。

## 源码与验证

分层源码在 `src/lego_workbench`；统一入口 `Run.ps1/run.py`，批量入口 `Batch.ps1/batch.py`。必要 vendor、字体、配置模板、VS Code 工作区和测试一并提供。复用索引只核验成品来源/完整性，不生成或伪造原流程认证标记。

vendor 是团队已有源码快照，原快照未包含上游许可证，未推定/补造授权；见 `vendor/legolization060/NOTICE.md` 和 `docs/source_provenance.json`。字体许可在 `assets/fonts`。外层兼容代码使用固定 0.6.0 的 `_step_layout` 还原前缀，未修改 vendor Python 源码或原 site-packages。

测试只用合成积木、短时限与模拟进程，不读取/重跑旧课程模型：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\Run.ps1 -InputModel .\tests\fixtures\three_bricks.ldr -Title "三块合成样例" -AnalysisSeconds 0.2
```

退出码 0 表示静力项目通过；2 表示说明书已保存，稳定性有风险/未完成/异常；1 表示输入、环境或图文失败。批量逐模型记录，失败不会静默跳过。

交付不含虚拟环境、整套 LDraw、LeoCAD 安装文件、视频、权重、凭据、Git 或渲染缓存。完整工程 ZIP 中的 `workbench` 与同级 `instructions` 成品目录配套使用。
