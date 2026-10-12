"""Clone upstream sources at the recorded revisions; never reset existing clones."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-unitex", action="store_true", help="also fetch local reference UniTEX sources")
    args = parser.parse_args()
    projects = json.loads((ROOT / "upstream.lock.json").read_text(encoding="utf-8"))
    for relative, project in projects.items():
        if relative == "external/UniTEX" and not args.include_unitex:
            continue
        target = ROOT / relative
        if target.exists():
            current = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
            if current != project["revision"]:
                raise SystemExit(f"Existing checkout has a different revision: {relative}; review it manually")
            print(f"Keep existing checkout: {relative} ({current})")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", project["url"], str(target)], check=True)
        subprocess.run(["git", "-C", str(target), "checkout", "--detach", project["revision"]], check=True)


if __name__ == "__main__":
    main()
