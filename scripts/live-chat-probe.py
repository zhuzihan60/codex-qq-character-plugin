"""One budgeted production-prompt diagnostic. Never sends QQ or applies output."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from qq_character.config import load
from qq_character.engine import Engine, POLICY
from qq_character.runtime import Runtime
from qq_character.service import ProcessLock
from qq_character.store import Store, dumps

config = load(Path("config/local.json"))
limit = 24000
scope = f"onebot:{config['transport']['bot_id']}:private:{config['transport']['allowed_private'][0]}"
started = time.monotonic()
report = {"verification": "REAL_PRODUCTION_PROMPT_DIAGNOSTIC", "qq_messages_sent": 0,
          "output_applied": False, "max_model_turns": 1, "token_target": limit, "timeout_seconds": 75,
          "started_at": datetime.now(timezone.utc).isoformat(), "tools": []}
with ProcessLock(Path(config["database"]).with_suffix(".lock")):
    store = Store(config["database"])
    runtime = Runtime(config["runtime"])
    engine = Engine(config, store, runtime)
    try:
        event = store.rows("SELECT * FROM events WHERE scope=? AND kind='message' ORDER BY seq DESC LIMIT 1", (scope,))[0]
        data = engine.context(event)
        prompt = POLICY + "\n" + dumps({"mode": "chat", "identity": config["identity"], "data": data})
        rid = store.reserve(scope, "deployment_diagnostic", str(engine.local(time.time()).date()),
                            limit, config["budgets"]["daily_tokens"], config["budgets"]["daily_model_calls"])
        if not rid:
            raise RuntimeError("Daily budget unavailable")
        def tool(name, args):
            report["tools"].append(name)
            if name == "memory_search":
                return {"memories": store.retrieve(scope, event["sender"], str(args.get("query", ""))[:200])}
            return {"error": "This diagnostic permits scoped memory retrieval only"}
        try:
            result = runtime.run(prompt, scope=scope, token_budget=limit, timeout_s=75, tool_handler=tool)
            store.account(rid, result["usage_tokens"])
            report.update(passed=True, usage_tokens=result["usage_tokens"], reply=result["output"]["reply"],
                          action=result["output"]["action"])
        except Exception as exc:
            usage = getattr(exc, "usage_tokens", None)
            if type(usage) is int and usage >= 0:
                store.account(rid, usage)
            report.update(passed=False, error=type(exc).__name__, usage_tokens=usage)
        report["elapsed_seconds"] = round(time.monotonic()-started, 2)
        Path(".state/live-chat-probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        store.close()
