# 中文乐高拼搭说明书

本目录汇总9个新成品规格和用户指定的小鸡、兔子PDF，共11份PDF、9份HTML。发布于2026-10-26，全部复用已完成成果，没有重新规划、渲染或启动稳定性求解。

## 阅读方式

GitHub可直接预览PDF。HTML请下载对应模型的整个目录后，在浏览器打开 instructions_cn.html，并保留 steps、parts、details 的相对位置。GitHub仓库页面显示HTML源码，不作为网页托管。新模型各目录另附BOM、稳定性摘要和风险报告。

## 两项状态分别记录

“说明书生成流程测试通过”表示零件/步骤覆盖、图片完整性、PDF可打开与中文字体、既有视觉验收及HTML引用均已核验，不表示稳定性通过。所有风险和草稿标记保留，没有把图文成功作为认证。

“稳定性验证未完成”对应逐步骤检查未完成；最终模型静力结论仍单列。已完成检查但发现风险的版本继续保留未通过结论。计划中的风险标签不等于完整逐步验证，未标记风险不等于通过。小鸡、兔子仅原样复用用户指定PDF，其状态来自历史记录。

| 模型 | 规格 | 件数 | 步数 | PDF页数 | 说明书 | 生成状态 | 稳定性状态 |
|---|---|---:|---:|---:|---|---|---|
| 狐狸 8 凸点版 | 8 凸点版 | 65 | 12 | 24 | [PDF](Fox_08stud/instructions_cn.pdf) · [HTML](Fox_08stud/instructions_cn.html) | 说明书生成流程测试通过（已有成品复用） | 已完成验证，存在风险，未通过稳定认证；最终模型静力未通过 |
| 橙子 8 凸点版 | 8 凸点版 | 205 | 51 | 67 | [PDF](Orange_08stud/instructions_cn.pdf) · [HTML](Orange_08stud/instructions_cn.html) | 说明书生成流程测试通过（已有成品复用） | 已完成验证，存在风险，未通过稳定认证；最终模型静力通过 |
| 苹果 8 凸点版 | 8 凸点版 | 286 | 73 | 89 | [PDF](Apple_08stud/instructions_cn.pdf) · [HTML](Apple_08stud/instructions_cn.html) | 说明书生成流程测试通过（已有成品复用） | 已完成验证，存在风险，未通过稳定认证；最终模型静力通过 |
| 狐狸 18 凸点版 | 18 凸点版 | 336 | 50 | 66 | [PDF](Fox_18stud/instructions_cn.pdf) · [HTML](Fox_18stud/instructions_cn.html) | 说明书生成流程测试通过（已有成品复用） | 稳定性验证未完成；最终模型静力未通过 |
| 狐狸 24 凸点版 | 24 凸点版 | 483 | 71 | 88 | [PDF](Fox_24stud/instructions_cn.pdf) · [HTML](Fox_24stud/instructions_cn.html) | 说明书生成流程测试通过（已有成品复用） | 已完成验证，存在风险，未通过稳定认证；最终模型静力未通过 |
| 橙子 14 凸点版 | 14 凸点版 | 587 | 105 | 110 | [PDF](Orange_14stud/instructions_cn.pdf) · [HTML](Orange_14stud/instructions_cn.html) | 说明书生成流程测试通过（已有成品复用） | 稳定性验证未完成；最终模型静力通过 |
| 狐狸 27 凸点版 | 27 凸点版 | 615 | 96 | 113 | [PDF](Fox_27stud/instructions_cn.pdf) · [HTML](Fox_27stud/instructions_cn.html) | 说明书生成流程测试通过（已有成品复用） | 稳定性验证未完成；最终模型静力未通过 |
| 狐狸 30 凸点版 | 30 凸点版 | 697 | 103 | 121 | [PDF](Fox_30stud/instructions_cn.pdf) · [HTML](Fox_30stud/instructions_cn.html) | 说明书生成流程测试通过（已有成品复用） | 稳定性验证未完成；最终模型静力未通过 |
| 狐狸 32 凸点版 | 32 凸点版 | 796 | 121 | 139 | [PDF](Fox_32stud/instructions_cn.pdf) · [HTML](Fox_32stud/instructions_cn.html) | 说明书生成流程测试通过（已有成品复用） | 稳定性验证未完成；最终模型静力未通过 |
| 小鸡 | 用户指定历史版 | 273 | 59 | 63 | [PDF](Chicken_legacy/Chicken_Instructions_CN.pdf) | 指定PDF原样复用并核验 | 历史最终静力通过，中间及插入风险保留；本次未重新计算 |
| 兔子 | 用户指定历史版 | 820 | 121 | 138 | [PDF](Bunny_legacy/Bunny_instructions_cn.pdf) | 指定PDF原样复用并核验 | 历史最终静力未通过，风险保留；本次未重新计算 |

## 复用、时限与未完成项

本次每模型新增稳定性计算为0秒，直接复用已有步骤、BOM、检查点及图文。稳定性相关计算采用每模型累计最多1800秒的口径，不能为每次LP或重新启动验证重置预算；预算耗尽时保留结果，优先输出带“稳定性验证未完成”标记的草稿。当前所有说明书已完成，无需为发布继续运行历史超时模型。

仍未完成逐步骤稳定性验证：狐狸18、27、30、32凸点版及橙子14凸点版。狐狸30为697件、103步、121页，原计划直接复用。橙子14沿用原MPD及子模型STEP的105步顺序，未重新求解或设计积木布局。

## 排除与文件范围

排除误打包7ken旧版本及原model(1).mpd说明书，原输入SHA256为 `dbf917b222136797636177c8ce1a104581e050f962f94c6b04ccc1b33790d056`。狐狸8的相同内容只收录一次。其他新模型即使以model.mpd命名也正常收录，按来源和输入哈希识别。

只发布阅读所需的PDF、HTML、引用图片、BOM及稳定性摘要/报告和验收清单。不含虚拟环境、整套LDraw、LeoCAD、视频、权重、ZIP重复包、原始机器路径或无关缓存。模型图文来自团队已有课程成品；原StableLego/Gurobi记录不冒充本次legolization0.6.0结论，本次未运行旧实验或稳定性优化。

机器可读状态及输入/PDF哈希见 [manifest.json](manifest.json)，发布文件哈希见 [checksums.sha256](checksums.sha256)，复用和引用校验见 [validation.json](validation.json)。原始本地工程、分析计划及历史结果保留。
