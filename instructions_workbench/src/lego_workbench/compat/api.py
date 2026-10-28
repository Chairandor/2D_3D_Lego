"""Verified public legolization 0.6.0 signatures (from the original adapter)."""
import sys
import importlib
import inspect

def load_api():
    if sys.version_info < (3, 12):
        raise RuntimeError("需要 Python 3.12 或更新版本；请运行项目 Setup.ps1 安装独立环境。")
    package = importlib.import_module("legolization")
    if package.__version__ != "0.6.0":
        raise RuntimeError(f"此适配包针对已核对的 0.6.0，当前版本为 {package.__version__!r}。")
    api = {
        "import_ldraw": ("legolization.ldraw_in", "import_ldraw", {"path", "ground"}),
        "write_model": ("legolization.ldraw_out", "write_model", {"layout", "path", "plan"}),
        "plan_instructions": ("legolization.instructions", "plan_instructions", {"layout", "config"}),
        "certify_instructions": ("legolization.instructions.verification", "certify_instructions", {"layout", "plan", "config"}),
        "render_step_images": ("legolization.instructions", "render_step_images", {"model_path", "plan", "config", "progress"}),
        "write_booklet": ("legolization.instructions", "write_booklet", {"plan", "stats", "images", "path", "config"}),
        "analyze": ("legolization.stability.solver", "analyze", {"layout", "config"}),
    }
    objects = {"package": package}
    for key, (module, name, arguments) in api.items():
        fn = getattr(importlib.import_module(module), name)
        if not arguments <= set(inspect.signature(fn).parameters):
            raise RuntimeError(f"接口签名与源码不一致：{module}.{name}{inspect.signature(fn)}")
        objects[key] = fn
    for name in ("InstructionsConfig", "BookletConfig", "ModelStats", "StepImages", "RenderConfig", "Renderer", "detect_renderer", "detect_ldraw_dir"):
        objects[name] = getattr(importlib.import_module("legolization.instructions"), name)
    objects["SolverConfig"] = importlib.import_module("legolization.stability.solver").SolverConfig
    objects["ConnectionGraph"] = importlib.import_module("legolization.graph").ConnectionGraph
    return objects
