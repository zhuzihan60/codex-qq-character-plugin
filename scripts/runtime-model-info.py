"""Read isolated App Server model settings without inference, QQ, or auth-file access."""
import argparse
import json
from pathlib import Path

from qq_character.runtime import diagnose_runtime


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="查询专属 Codex 的实际模型与推理强度，不执行推理")
    parser.add_argument("--config", type=Path, default=root / "config/local.json")
    parser.add_argument("--runtime-default", action="store_true",
                        help="仅查询后端原始默认值，不施加角色的 reasoning_effort 设置")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))["runtime"]
        report = diagnose_runtime(config, timeout_s=45, use_runtime_default=args.runtime_default)
        summary = {
            "verification": "REAL_APP_SERVER_NO_INFERENCE", "passed": report.get("live_ready", False),
            "configuration_mode": "runtime_default" if args.runtime_default else "configured",
            "model": report.get("model"),
            "authenticated": report.get("authentication", {}).get("authenticated", False),
            "discovery_ok": report.get("discovery", {}).get("ok", False),
            "model_inference_performed": False, "qq_messages_sent": 0,
            "errors": report.get("errors", []),
        }
    except Exception as exc:
        summary = {"passed": False, "error_type": type(exc).__name__,
                   "model_inference_performed": False, "qq_messages_sent": 0}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
