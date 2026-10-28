# 来源与许可

本项目是对多个上游项目的编排和外部适配，未包含上游 Git 历史、模型权重或零件库。

| 组件 | 来源 | 在本项目中的用途 |
| --- | --- | --- |
| Hunyuan3D-2 / 2mini | Tencent-Hunyuan/Hunyuan3D-2；tencent/Hunyuan3D-2mini | 无色几何生成 |
| UniTEX | YixunLiang/UniTEX；lyxun/UniTEX | 六视图着色及 LTM 投影 |
| FLUX.1-dev | black-forest-labs/FLUX.1-dev | UniTEX 六视图模型底座 |
| Legolization | hbmartin/legolization | 体素化、积木求解与 MPD 输出 |
| SAM 2 | facebookresearch/sam2 | 主体分割 |
| Florence-2 | microsoft/Florence-2-large-ft | 主体定位 |
| LDraw | [LDraw.org 官方零件库](https://library.ldraw.org/updates) | LEGO 零件与颜色数据；本地 MPD 预览按需读取项目内 `LDrawLibrary/ldraw/`，零件库不纳入仓库，获取与放置方法见 `docs/SETUP.md` |
| Three.js 0.186.1 | mrdoob/three.js，MIT 许可证 | 本地网页中的 GLB/PLY/MPD 交互预览；许可文本见 `web_assets/three/LICENSE` |
| 说明书运行源码 | 本仓库 `instructions` 分支，`a91c8c1` | `instructions_workbench/src/lego_workbench` 保留队友的规划、渲染、图文与静力分析实现；主分支以根目录 `ui_instructions.py` 接入，未合并历史模型和结果 |
| Legolization 0.6.0 说明书快照 | 同上分支提供的已安装包源码快照 | 隔离在 `instructions_workbench/vendor/legolization060`，按上游 GPL-3.0-or-later 条款保留；许可原文与来源见该目录 `LICENSE.txt` / `NOTICE.md`。这是本仓库包含的必要第三方源码例外，主流水线的上游 checkout 仍通过版本锁获取 |
| Droid Sans Fallback | Google / Ascender，Apache-2.0 | 中文 PDF 字体，许可和来源见 `instructions_workbench/assets/fonts/FONT_LICENSE.txt` / `FONT_NOTICE.txt` |

源码版本见 `upstream.lock.json`。请分别查看下载版本的 LICENSE、模型卡和使用条件；某些模型需账号接受许可后下载。公开仓库不包含账号 token、私钥、获批权重或用户素材。

这里没有为整条链路指定统一开源许可证。自写代码的正式许可证尚待维护者决定；第三方源码和权重的条件仍由各自项目决定。
