"""Explicit owner-run real inference check. No QQ transport and no archive import."""
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from qq_character.config import load
from qq_character.runtime import Runtime

root = Path(__file__).resolve().parents[1]
config = load(root / 'config/local.json')
runtime = Runtime(config['runtime'])
prompt = (
    '这是部署者亲自发起的一次真实连通性验收，不是 QQ 消息。'
    '只回复用户的招呼，不创建记忆或行为修改，不调用工具，不追加问题。'
    '按 outputSchema 输出 JSON，其他数组/文字字段可以为空。\n'
    + json.dumps({'identity': config['identity'], 'message': '你好，汐。今天先简单打个招呼，不用反问我。'}, ensure_ascii=False)
)
report = {'started_at': datetime.now(timezone.utc).isoformat(),
          'verification': 'REAL_CODEX_APP_SERVER_INFERENCE', 'qq_messages_sent': 0,
          'max_model_turns': 1, 'token_target': config['budgets']['turn_tokens'],
          'timeout_seconds': config['budgets']['turn_seconds']}
started = time.monotonic()
try:
    result = runtime.run(prompt, scope='deployment:smoke',
                         timeout_s=config['budgets']['turn_seconds'],
                         token_budget=config['budgets']['turn_tokens'])
    report.update(passed=True, reply=result['output']['reply'],
                  action=result['output']['action'], usage_tokens=result['usage_tokens'],
                  diagnostics=runtime.last_diagnostics)
except Exception as exc:
    report.update(passed=False, error_type=type(exc).__name__, error=str(exc),
                  diagnostics=runtime.last_diagnostics)
report['elapsed_seconds'] = round(time.monotonic() - started, 2)
target = root / '.state/live-smoke.json'
target.parent.mkdir(exist_ok=True)
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report['passed'] else 1)
