"""Execute OpenMask3D in a configured GPU environment, locally or on the team server."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import subprocess


def available() -> bool:
    """Report whether an existing OpenMask3D installation has been configured."""
    return all(os.environ.get(name) for name in ("OPENMASK3D_ROOT", "OPENMASK3D_PYTHON", "OPENMASK3D_CHECKPOINT"))


def _run(command: list[str], timeout: int) -> str:
    try:
        result = subprocess.run(command, timeout=timeout, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"OpenMask3D 执行未完成：{exc}") from exc
    if result.returncode:
        raise RuntimeError("OpenMask3D 执行失败：" + (result.stderr.strip() or result.stdout.strip())[-1800:])
    return result.stdout


def scene_catalog(root: Path) -> dict[str, dict]:
    """Read the administrator's named mappings to existing scene results."""
    path = Path(os.environ.get("OPENMASK3D_SCENE_CATALOG", "openmask_scenes.local.json"))
    path = path if path.is_absolute() else root / path
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("OpenMask3D 场景清单需要是 JSON 对象")
    for name, row in data.items():
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not isinstance(row, dict)
                or not all(isinstance(row.get(key), str) and row[key].strip() for key in ("label", "scene", "masks"))):
            raise ValueError("OpenMask3D 场景清单每条需要有效的 ID、label、scene 和 masks")
        if Path(row["scene"]).suffix.lower() != ".ply" or Path(row["masks"]).suffix.lower() not in {".pt", ".npy", ".npz"}:
            raise ValueError("OpenMask3D 清单需指定场景 PLY 和 PT、NPY 或 NPZ 掩码")
    return data


def has_query(scene: dict) -> bool:
    """Report whether both saved features and their local CLIP checkpoint are configured."""
    return all(isinstance(scene.get(key), str) and scene[key] for key in ("features", "clip_weights")) and bool(os.environ.get("OPENMASK3D_PYTHON"))


def _ssh_options(host: str) -> list[str]:
    if not re.fullmatch(r"[a-zA-Z0-9_.@-]+", host) or host.startswith("-"):
        raise ValueError("OPENMASK3D_SSH 需要是已配置的 SSH 别名或 user@host")
    options = ["-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=10"]
    if key := os.environ.get("OPENMASK3D_SSH_KEY"):
        options = ["-i", key, "-o", "IdentitiesOnly=yes", *options]
    return options


def _remote_worker(host: str, worker: Path) -> PurePosixPath:
    project = os.environ.get("OPENMASK3D_ROOT", "")
    job_root = os.environ.get("OPENMASK3D_JOB_ROOT") or (project + "/output/legolizer_ui" if project else "")
    remote_root = PurePosixPath(job_root)
    if not remote_root.is_absolute() or ".." in remote_root.parts:
        raise ValueError("请配置数据盘上的 OPENMASK3D_ROOT 或 OPENMASK3D_JOB_ROOT")
    remote = remote_root / secrets.token_hex(8)
    transport = _ssh_options(host)
    _run(["ssh", *transport, host, shlex.join(["mkdir", "-p", "--", str(remote)])], 20)
    _run(["scp", *transport, str(worker), f"{host}:{remote / 'worker.py'}"], 30)
    return remote


def stage_scene(row: dict, folder: Path, worker: Path) -> tuple[Path, Path]:
    """Reuse a catalogued PLY and its saved masks through the existing portable reader."""
    source, saved = row["scene"], row["masks"]
    suffix = Path(saved).suffix.lower()
    scene = folder / "scene.ply"
    masks = folder / ("masks.npz" if suffix == ".pt" else "masks" + suffix)
    host = os.environ.get("OPENMASK3D_SSH")
    python = os.environ.get("OPENMASK3D_PYTHON")
    if suffix == ".pt" and not python:
        raise ValueError("读取服务器的 OpenMask3D PT 掩码需要设置 OPENMASK3D_PYTHON")
    folder.mkdir(parents=True)
    if not host:
        shutil.copyfile(source, scene)
        if suffix == ".pt":
            _run([python, str(worker), str(scene), str(masks), "--saved-mask", saved], 120)
        else:
            shutil.copyfile(saved, masks)
        return scene, masks

    transport = _ssh_options(host)
    if not PurePosixPath(source).is_absolute() or not PurePosixPath(saved).is_absolute():
        raise ValueError("服务器场景和掩码需要配置绝对路径")
    if suffix == ".pt":
        remote = _remote_worker(host, worker)
        portable = str(remote / "masks.npz")
        args = [python, str(remote / "worker.py"), source, portable, "--saved-mask", saved]
        _run(["ssh", *transport, host, shlex.join(args)], 120)
        saved = portable
    _run(["scp", *transport, f"{host}:{source}", str(scene)], 180)
    _run(["scp", *transport, f"{host}:{saved}", str(masks)], 180)
    return scene, masks


def rank_instances(row: dict, query: str, worker: Path) -> list[float | None]:
    """Encode a text prompt against the registered scene's existing mask features."""
    if not has_query(row):
        raise ValueError("该场景还没有配置已算好的实例特征和对应的 CLIP 权重")
    python = os.environ["OPENMASK3D_PYTHON"]
    args = ["query", row["features"], row["clip_weights"], "--", query]
    if host := os.environ.get("OPENMASK3D_SSH"):
        remote = _remote_worker(host, worker)
        command = ["ssh", *_ssh_options(host), host, shlex.join([python, str(remote / "worker.py"), *args])]
    else:
        command = [python, str(worker), *args]
    return json.loads(_run(command, 120).splitlines()[-1])


def generate_masks(scene: Path, output: Path, worker: Path) -> None:
    """Generate a mask NPZ next to the uploaded scene using only server-side settings."""
    if not available():
        raise ValueError("尚未配置 OpenMask3D 服务端，请上传同一场景的已导出掩码")
    if output.is_file():
        return
    project = os.environ["OPENMASK3D_ROOT"]
    python = os.environ["OPENMASK3D_PYTHON"]
    checkpoint = os.environ["OPENMASK3D_CHECKPOINT"]
    options = ["--project", project, "--checkpoint", checkpoint]
    if (gpu := os.environ.get("OPENMASK3D_GPU")) is not None:
        options.extend(["--gpu", gpu])
    host = os.environ.get("OPENMASK3D_SSH")
    if not host:
        _run([python, str(worker), str(scene), str(output), *options], 960)
        return
    remote = _remote_worker(host, worker)
    transport = _ssh_options(host)
    ssh = ["ssh", *transport, host]
    _run(["scp", *transport, str(scene), f"{host}:{remote / 'scene.ply'}"], 180)
    args = [python, str(remote / "worker.py"), str(remote / "scene.ply"), str(remote / "masks.npz"), *options]
    _run([*ssh, shlex.join(args)], 960)
    temporary = output.with_name(output.stem + "_transfer.npz")
    _run(["scp", *transport, f"{host}:{remote / 'masks.npz'}", str(temporary)], 180)
    temporary.replace(output)
