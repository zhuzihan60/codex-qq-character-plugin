"""Explicit operator setup: pin the freshly built container image in local config."""
import json
from pathlib import Path
import subprocess
import sys
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("config", nargs="?", default="config/local.json")
parser.add_argument("--development", action="store_true")
args = parser.parse_args()
target = Path(args.config)
if not target.exists():
    raise SystemExit("Create config/local.json from config/example.json first.")
data = json.loads(target.read_text(encoding="utf-8-sig"))
runtime = data["runtime"].get("development") if args.development else data["runtime"]
if not runtime:
    raise SystemExit("Missing separate development profile")
container = runtime["isolation"]["container"]
result = subprocess.run([runtime.get("docker_command", "docker"), "inspect", "--format", "{{.Image}}", container],
                        capture_output=True, text=True, check=True, timeout=15)
image = result.stdout.strip()
if not image.startswith("sha256:") or len(image) != 71:
    raise SystemExit("Unexpected Docker image identifier")
runtime["isolation"]["image_id"] = image
target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("Pinned runtime image. Run diagnostics before live inference.")
