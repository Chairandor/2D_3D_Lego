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
| LDraw | ldraw.org | LEGO 零件与颜色数据；本地 MPD 预览按需读取零件库，零件库不纳入仓库 |
| Three.js 0.186.1 | mrdoob/three.js，MIT 许可证 | 本地网页中的 GLB/PLY/MPD 交互预览；许可文本见 `web_assets/three/LICENSE` |

源码版本见 `upstream.lock.json`。请分别查看下载版本的 LICENSE、模型卡和使用条件；某些模型需账号接受许可后下载。公开仓库不包含账号 token、私钥、获批权重或用户素材。

这里没有为整条链路指定统一开源许可证。自写代码的正式许可证尚待维护者决定；第三方源码和权重的条件仍由各自项目决定。
