"""Offline development-entrypoint tests. No Codex process or QQ connection."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

from qq_character import cli
from qq_character.skills import SkillLab
from qq_character.store import Store


SCOPE = "onebot:100:private:200"
OTHER = "onebot:100:group:300"
ADVICE = ("scope: current_conversation\nevidence: source_events_only\n"
          'evidence_ids: ["event:correction"]\n'
          "对方明确纠正之后，按最近的原始纠正调整称呼。证据不足时保留不确定性；"
          "问题已经解决就自然收尾，不必总是追加提问。")


class FakeRuntime:
    """A test double whose output must never be mistaken for live validation."""
    def __init__(self):
        self.ready = True
        self.failure = None
        self.calls = []
        self.proposal = ADVICE

    def diagnose(self, **kwargs):
        self.diagnostic_mode = kwargs["mode"]
        return {"live_ready": self.ready, "test_double": True}

    def run(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.failure:
            raise self.failure
        return {"usage_tokens": 12, "output": {
            "action": "reply", "reply": "不要向 QQ 转发这条开发日志",
            "memories": [{"content": "模型要求写入的任意记忆"}],
            "behavior": [{"key": "interest", "value": "任意变化"}],
            "reflection": "模型开发日志", "skill_proposal": self.proposal,
        }}


class DevelopmentCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = json.loads((cli.ROOT / "config/example.json").read_text(encoding="utf-8"))
        self.config["database"] = str(self.root / "character.db")
        self.config["skill_lab"] = str(self.root / "lab")
        self.config["transport"].update(bot_id="100", allowed_private=["200"], allowed_groups=["300"])
        self.config["runtime"]["development"] = {"test_profile": True}
        self.task = self.root / "task.txt"
        self.task.write_text("评审减少无目的追问的建议。", encoding="utf-8")
        self.args = SimpleNamespace(scope=SCOPE, task_file=str(self.task))
        store = Store(self.config["database"])
        now = time.time()
        store.ingest("event:correction", SCOPE, "message", "回答结束不必总是问我问题。", "200", now)
        store.ingest("event:private-other", OTHER, "message", "OTHER_SCOPE_SECRET_SENTINEL", "400", now)
        store.remember(OTHER, "400", {"kind": "episodic", "content": "OTHER_SCOPE_SECRET_SENTINEL",
                                     "key": "secret", "confidence": 0.8,
                                     "evidence_ids": ["event:private-other"]})
        store.close()
        self.runtime = FakeRuntime()
        self.emitted = []
        self.runtime_patch = patch.object(cli, "Runtime", return_value=self.runtime)
        self.emit_patch = patch.object(cli, "emit", side_effect=self.emitted.append)
        self.runtime_constructor = self.runtime_patch.start()
        self.emit_patch.start()
        self.addCleanup(self.runtime_patch.stop)
        self.addCleanup(self.emit_patch.stop)

    def rows(self, sql):
        store = Store(self.config["database"])
        try:
            return store.rows(sql)
        finally:
            store.close()

    def test_scoped_candidate_is_checked_without_applying_model_side_effects(self):
        self.assertEqual(cli.develop(self.config, self.args), 0)
        result = self.emitted[-1]
        self.assertTrue(result["candidate_checks_passed"])
        self.assertFalse(result["promoted"])
        lab = SkillLab(self.config["skill_lab"])
        self.assertEqual(lab.active_instructions(SCOPE), "")
        self.assertEqual(lab.active_instructions(OTHER), "")
        candidate = json.loads((self.root / "lab/candidates" / (result["candidate_id"] + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(candidate["scope"], SCOPE)
        self.assertEqual(candidate["evidence_ids"], ["event:correction"])
        self.assertNotIn("evidence_ids:", candidate["instructions"])
        prompt, options = self.runtime.calls[0]
        self.assertNotIn("OTHER_SCOPE_SECRET_SENTINEL", prompt)
        self.assertEqual(options["scope"], SCOPE)
        self.assertEqual(options["mode"], "development")
        self.assertEqual(self.runtime.diagnostic_mode, "development")
        self.assertEqual(options["token_budget"], self.config["budgets"]["turn_tokens"])
        self.assertEqual(options["timeout_s"], self.config["budgets"]["turn_seconds"])
        for table in ("outbox", "behavior", "reflections"):
            self.assertEqual(self.rows(f"SELECT * FROM {table}"), [])
        self.assertEqual(len(self.rows("SELECT * FROM memories")), 1)
        self.assertEqual(self.rows("SELECT mode FROM reservations"), [{"mode": "development"}])

    def test_cross_scope_or_invented_proposal_evidence_is_rejected(self):
        for reference in ("event:private-other", "invented:evidence"):
            self.runtime.proposal = ADVICE.replace("event:correction", reference)
            self.assertEqual(cli.develop(self.config, self.args), 0)
            self.assertTrue(self.emitted[-1]["candidate_rejected"])
            self.assertIsNone(self.emitted[-1]["candidate_id"])
        self.assertFalse((self.root / "lab").exists())

    def test_unallowlisted_or_unknown_scope_cannot_start_runtime(self):
        self.args.scope = "onebot:100:private:999"
        with self.assertRaises(ValueError):
            cli.develop(self.config, self.args)
        self.config["transport"]["allowed_private"].append("999")
        with self.assertRaises(ValueError):
            cli.develop(self.config, self.args)
        self.runtime_constructor.assert_not_called()

    def test_failed_diagnostics_do_not_spend_or_run(self):
        self.runtime.ready = False
        self.assertEqual(cli.develop(self.config, self.args), 2)
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(self.rows("SELECT * FROM reservations"), [])

    def test_failure_retains_budget_and_prevents_unlimited_retry(self):
        self.config["budgets"]["daily_model_calls"] = 1
        self.runtime.failure = TimeoutError("fake model timeout")
        with self.assertRaises(TimeoutError):
            cli.develop(self.config, self.args)
        with self.assertRaises(RuntimeError):
            cli.develop(self.config, self.args)
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertEqual(len(self.rows("SELECT * FROM reservations")), 1)
        self.assertEqual(self.rows("SELECT * FROM outbox"), [])

    def test_oversized_task_is_rejected_before_runtime(self):
        self.task.write_text("读" * 8001, encoding="utf-8")
        with self.assertRaises(ValueError):
            cli.develop(self.config, self.args)
        self.runtime_constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
