# 网页拼搭说明书运行工程

本目录取自本仓库队友的 `instructions` 分支提交 `a91c8c183a3f7e6c8bc842363fd8d08410e4610a`，保留原生规划、LeoCAD 渲染、中文 PDF/HTML 和累计限时静力分析。此主分支版本仅包含程序、测试、字体和依赖，不包含该分支的课程模型、历史检查点和已发布说明书。原 `instructions` 分支仍独立保留。

## 安装

前置条件：Windows、uv、Python 3.12、LeoCAD 和完整 LDraw 库。先按 [部署说明](../docs/SETUP.md) 把零件库放到项目的 `LDrawLibrary/ldraw/`。

在项目根目录运行：

```powershell
uv venv --python 3.12 --seed instructions_workbench/.venv
cd instructions_workbench
powershell -NoProfile -ExecutionPolicy Bypass -File .\Setup.ps1
Copy-Item config/portable.example.json config/local.json
notepad config/local.json
```

把 `renderer` 改成自己的 `LeoCAD.exe` 路径。`ldraw_dir` 默认 `../LDrawLibrary/ldraw`，`output_root` 默认 `../runs/lego-instructions`，相对路径以本目录为基准。已有配置不要覆盖。通过 uv 先创建环境可避免 Windows Python Launcher 未识别 uv Python 的问题；`--seed` 确保原安装脚本能调用 pip。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Run.ps1 -Mode check
```

环境独立安装锁定依赖和 `vendor/legolization060`，不会替换主流水线的 Legolization 环境。LeoCAD、LDraw 库及虚拟环境不随 GitHub 分发。

## 从 UI 使用

回到项目根目录启动 `pipeline_ui.bat`。点击 LEGO 模型卡片名称，在覆盖页选择「生成说明书」。已有相同输入 SHA256 的完整图文时直接打开，没有时自动排队生成。左边为当前步骤图，右边为本步零件，右下方翻步，右上方可下载完整 PDF。成品与总清单位于「成品展示」，该页右上方可单独导出带缩略图的清单 PDF。

UI 由根目录 `ui_instructions.py` 调用原生规划、渲染与分析接口，跳过课程项目的历史模型排除名单和历史成品索引。程序保留源模型的零件、颜色、位置和变换，不重建或优化几何。步骤优先沿用源 STEP，无 STEP 时用无求解的分层顺序安排，因此图文生成成功不等于拼搭稳定。

图文生成后即可查看，随后独立分析最终模型和步骤静力，共享最多 1800 秒预算。状态可能为通过、未通过、超时或异常；风险或超时仍保留图文。关闭覆盖页不会停止生成。正常使用请保持 UI 服务终端开启，生成期间不要重启服务。

## 独立运行与测试

UI 使用的同一路线，在项目根目录也可执行：

```powershell
.\instructions_workbench\.venv\Scripts\python.exe .\ui_instructions.py --native-generate .\instructions_workbench C:\Models\model.mpd "我的模型"
```

普通模型也可通过 `Run.ps1 -InputModel C:\Models\model.mpd -Title "我的模型"` 使用队友原始命令入口。该入口仍保留其历史排除规则；网页使用的适配入口不受这份旧名单影响。原始批量成品复用不属于此主分支部署包。

合成样例与回归测试在 `tests/`，不读取用户模型：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

退出码 0 表示静力检查通过，2 表示图文已保存但稳定性有风险/未完成，1 表示发生异常。来源与第三方许可见根目录 [THIRD_PARTY.md](../THIRD_PARTY.md)、`vendor/legolization060/NOTICE.md` 及 `assets/fonts/`。
