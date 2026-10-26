"""Offline acceptance runner. Does not call live Codex, QQ or external sources."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import unittest

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
suite=unittest.defaultTestLoader.discover(str(root/'tests'))
result=unittest.TextTestRunner(verbosity=1).run(suite)
report={'time_utc':datetime.now(timezone.utc).isoformat(),'tests':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful(),
        'verification_kind':'OFFLINE_TESTS_WITH_REAL_LOOPBACK_WEBSOCKET',
        'codex_inference':'mocked','qq_client':'simulated OneBot websocket client',
        'sources':'fixtures','real_qq_messages_sent':0}
out=root/'.state'/'test-report.json';out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
