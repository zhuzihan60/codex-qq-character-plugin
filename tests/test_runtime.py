"""Deterministic App Server protocol mocks; these are NOT real model inference."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from qq_character.engine import Engine
from qq_character.isolation import (IsolationError, _check_container, audit_discovery,
                                    check_thread_response, minimal_environment, verify_isolation, launch_command)
from qq_character.runtime import (JsonRpcProcess, OUTPUT_SCHEMA, Runtime,
                                  RuntimeBudgetExceeded, RuntimeFailure, RuntimeInterrupted, _validate_output,
                                  diagnose_runtime)
from qq_character.store import Store


def config():
    return {"home": "/home/character/chat", "cwd": "/workspace/chat", "model": "test-model",
            "instruction_roots": ["/home/character/chat", "/workspace/chat"],
            "allowed_skills": ["character", "character-memory"],
            "isolation": {"type": "docker", "container": "character-test"}}


def output():
    return {"action": "reply", "reply": "记住了，下次我会简短些。", "memories": [], "behavior": [],
            "reflection": "", "diary": "", "skill_proposal": ""}


def discoveries():
    return {"config/read": {"config": {"approval_policy": "never", "sandbox_mode": "read-only",
                                       "web_search": "disabled", "tools": {
                                           "update_plan": {"enabled": False},
                                           "experimental_request_user_input": {"enabled": False}},
                                       "features": {k: False for k in
                                       ["shell_tool", "apps", "hooks", "multi_agent", "memories", "goals", "remote_plugin"]}},
                            "layers": [{"name": {"file": "/home/character/chat/config.toml"}}]},
            "skills/list": {"data": [{"skills": [{"name": "character", "enabled": True,
                        "path": "/home/character/chat/skills/character/SKILL.md", "scope": "user"},
                        {"name": "character-memory", "enabled": True,
                        "path": "/home/character/chat/skills/character-memory/SKILL.md", "scope": "user"}], "errors": []}]},
            "mcpServerStatus/list": {"data": [], "nextCursor": None},
            "plugin/list": {"marketplaces": [], "marketplaceLoadErrors": []}}


class FakeRpc:
    def __init__(self, events=None, account_response=None):
        self.calls = []
        self.request_handler = None
        self.closed = False
        self.account_response = account_response if account_response is not None else {
            "account": {"type": "chatgpt", "email": "test@example.invalid", "planType": "plus"},
            "requiresOpenaiAuth": True}
        self.events = events if events is not None else [
            {"method": "item/completed", "params": {"threadId": "t-1", "turnId": "u-1", "item": {
                "id": "a-0", "type": "agentMessage", "phase": "commentary", "text": "PRIVATE TASK LOG"}}},
            {"method": "item/completed", "params": {"threadId": "t-1", "turnId": "u-1", "item": {
                "id": "a-1", "type": "agentMessage", "phase": "final_answer", "text": json.dumps(output())}}},
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "t-1", "turnId": "u-1",
                "tokenUsage": {"total": {"totalTokens": 421}}}},
            {"method": "turn/completed", "params": {"threadId": "t-1", "turn": {"id": "u-1", "status": "completed"}}},
        ]

    def send(self, message):
        self.calls.append(message)

    def request(self, method, params, deadline):
        self.calls.append({"method": method, "params": params})
        if method == "initialize":
            return {"userAgent": "fake-wire-only", "codexHome": "/home/character/chat"}
        if method in discoveries():
            return discoveries()[method]
        if method == "account/read":
            if isinstance(self.account_response, Exception):
                raise self.account_response
            return self.account_response
        if method == "thread/start":
            return {"thread": {"id": "t-1", "ephemeral": True}, "instructionSources": [],
                    "model": "test-model", "reasoningEffort": params.get("config", {}).get("model_reasoning_effort"),
                    "approvalPolicy": "never", "sandbox": {"type": "readOnly", "networkAccess": False}}
        if method == "model/list":
            return {"data": [
                {"id": "unrelated-model", "model": "unrelated-model", "defaultReasoningEffort": "high",
                 "supportedReasoningEfforts": [{"reasoningEffort": "high"}]},
                {"id": "test-model", "model": "test-model", "defaultReasoningEffort": "low",
                 "supportedReasoningEfforts": [{"reasoningEffort": value} for value in ("low", "medium", "high")]},
            ], "nextCursor": None}
        if method == "turn/start":
            return {"turn": {"id": "u-1"}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(method)

    def recv(self, deadline):
        value = self.events.pop(0)
        if isinstance(value, Exception):
            raise value
        if "id" in value and "method" in value:
            self.tool_result = self.request_handler(value)
            return self.recv(deadline)
        return value

    def close(self):
        self.closed = True


class RuntimeTests(unittest.TestCase):
    def run_fake(self, fake, **kwargs):
        with patch("qq_character.runtime.verify_isolation", return_value={"user": "10001", "container_id": "verified-container"}), \
             patch("qq_character.runtime.JsonRpcProcess", return_value=fake):
            return Runtime(config()).run("普通聊天", scope="qq:private:alice", **kwargs)

    def diagnose_fake(self, fake):
        with patch("qq_character.runtime.verify_isolation", return_value={"user": "10001", "container_id": "verified-container"}), \
             patch("qq_character.runtime.JsonRpcProcess", return_value=fake):
            return Runtime(config()).diagnose()

    def test_ready_diagnostic_reads_account_without_refresh_or_identity_leak(self):
        for auth_type in ["chatgpt", "apiKey", "amazonBedrock"]:
            with self.subTest(auth_type=auth_type):
                fake = FakeRpc(account_response={"account": {"type": auth_type,
                    "email": "PRIVATE-EMAIL", "accountId": "PRIVATE-ACCOUNT", "token": "PRIVATE-TOKEN"},
                    "requiresOpenaiAuth": True, "workspaceRouting": {"chatgptAccountId": "PRIVATE-ACCOUNT"}})
                result = self.diagnose_fake(fake)
                self.assertTrue(result["live_ready"])
                self.assertEqual(result["authentication"], {"checked": True, "authenticated": True,
                    "auth_type": auth_type, "requires_openai_auth": True})
                self.assertNotIn("PRIVATE-", json.dumps(result))
                account_calls = [c for c in fake.calls if c["method"] == "account/read"]
                self.assertEqual(account_calls, [{"method": "account/read", "params": {"refreshToken": False}}])
                self.assertFalse(result["model_inference_performed"])
                self.assertFalse(any(c["method"] in {"turn/start", "account/login/start"} for c in fake.calls))
                self.assertEqual(result["model"]["model"], "test-model")
                self.assertEqual(result["model"]["reasoning_effort"], "medium")
                self.assertEqual(result["model"]["supported_reasoning_efforts"], ["low", "medium", "high"])
                self.assertNotIn("unrelated-model", json.dumps(result))
                self.assertTrue(fake.closed)

    def test_default_model_query_omits_effort_override_and_does_not_infer(self):
        fake = FakeRpc()
        with patch("qq_character.runtime.verify_isolation", return_value={"user": "10001", "container_id": "verified-container"}), \
             patch("qq_character.runtime.JsonRpcProcess", return_value=fake):
            result = diagnose_runtime(config(), use_runtime_default=True)
        self.assertTrue(result["live_ready"])
        self.assertIsNone(result["model"]["reasoning_effort"])
        self.assertIsNone(result["model"]["requested_reasoning_effort"])
        self.assertEqual(result["model"]["catalog_default_reasoning_effort"], "low")
        started = next(c for c in fake.calls if c["method"] == "thread/start")["params"]
        self.assertNotIn("config", started)
        self.assertTrue(started["ephemeral"])
        self.assertEqual(started["dynamicTools"], [])
        self.assertFalse(any(c["method"] == "turn/start" for c in fake.calls))

    def test_configured_effort_is_sent_to_thread_and_each_turn_and_reported(self):
        for effort in ("medium", "high"):
            with self.subTest(effort=effort):
                cfg = {**config(), "reasoning_effort": effort}
                fake, runtime = FakeRpc(), Runtime(cfg)
                with patch("qq_character.runtime.verify_isolation", return_value={"user": "10001", "container_id": "verified-container"}), \
                     patch("qq_character.runtime.JsonRpcProcess", return_value=fake):
                    runtime.run("你好", scope="qq:private:alice")
                started = next(c for c in fake.calls if c["method"] == "thread/start")["params"]
                turn = next(c for c in fake.calls if c["method"] == "turn/start")["params"]
                self.assertEqual(started["config"], {"model_reasoning_effort": effort})
                self.assertEqual(turn["effort"], effort)
                self.assertEqual(runtime.last_diagnostics["model"], {
                    "model": "test-model", "reasoning_effort": effort, "requested_reasoning_effort": effort})

    def test_unhonored_effort_blocks_inference_and_live_ready(self):
        for diagnose in (False, True):
            with self.subTest(diagnose=diagnose):
                fake = FakeRpc()
                original_request = fake.request
                def request(method, params, deadline):
                    response = original_request(method, params, deadline)
                    if method == "thread/start":
                        response["reasoningEffort"] = "low"
                    return response
                fake.request = request
                if diagnose:
                    result = self.diagnose_fake(fake)
                    self.assertFalse(result["live_ready"])
                    self.assertIn("did not honor", result["errors"][-1])
                else:
                    with self.assertRaisesRegex(RuntimeFailure, "did not honor"):
                        self.run_fake(fake)
                self.assertFalse(any(c["method"] == "turn/start" for c in fake.calls))
                self.assertTrue(fake.closed)

    def test_unauthenticated_diagnostic_keeps_discovery_but_is_not_ready(self):
        for requires_auth in [True, False]:
            with self.subTest(requires_auth=requires_auth):
                fake = FakeRpc(account_response={"account": None, "requiresOpenaiAuth": requires_auth})
                result = self.diagnose_fake(fake)
                self.assertFalse(result["live_ready"])
                self.assertFalse(result["authentication"]["authenticated"])
                self.assertTrue(result["protocol_handshake"])
                self.assertTrue(result["discovery"]["ok"])
                self.assertEqual(result["isolation"]["container_id"], "verified-container")
                self.assertIn("not authenticated", result["errors"][0])

    def test_invalid_or_unavailable_account_check_fails_closed_after_discovery(self):
        for response in [RuntimeFailure("RPC account/read failed (code -1)"),
                         {"account": {"type": "chatgpt"}},
                         {"account": {"type": ["PRIVATE-UNKNOWN"]}, "requiresOpenaiAuth": True}]:
            with self.subTest(response_type=type(response).__name__):
                fake = FakeRpc(account_response=response)
                result = self.diagnose_fake(fake)
                self.assertFalse(result["live_ready"])
                self.assertTrue(result["protocol_handshake"])
                self.assertTrue(result["discovery"]["ok"])
                self.assertNotIn("PRIVATE-", json.dumps(result))
                self.assertTrue(fake.closed)

    def test_unauthenticated_local_probe_still_reports_protocol_and_discovery(self):
        fake = FakeRpc(account_response={"account": None, "requiresOpenaiAuth": True})
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {**config(), "home": str(Path(tmp) / "codex"), "cwd": str(Path(tmp) / "workspace"),
                   "isolation": {"type": "local-probe"}}
            with patch("qq_character.runtime.verify_isolation", side_effect=IsolationError("local probe has no OS isolation")), \
                 patch("qq_character.runtime.JsonRpcProcess", return_value=fake):
                result = Runtime(cfg).diagnose()
        self.assertTrue(result["protocol_handshake"])
        self.assertTrue(result["discovery"]["ok"])
        self.assertFalse(result["live_ready"])
        self.assertFalse(result["authentication"]["authenticated"])
        self.assertFalse(any(c["method"] == "turn/start" for c in fake.calls))

    def test_unauthenticated_run_stops_before_thread_or_turn_creation(self):
        fake = FakeRpc(account_response={"account": None, "requiresOpenaiAuth": True})
        with self.assertRaisesRegex(RuntimeFailure, "not authenticated"):
            self.run_fake(fake)
        self.assertFalse(any(c["method"] in {"thread/start", "turn/start"} for c in fake.calls))
        self.assertTrue(fake.closed)

    def test_json_only_fresh_ephemeral_no_task_logs(self):
        fake = FakeRpc()
        result = self.run_fake(fake)
        self.assertEqual(result["output"], output())
        self.assertEqual(result["usage_tokens"], 421)
        self.assertNotIn("PRIVATE TASK LOG", json.dumps(result))
        self.assertTrue(fake.closed)
        start = next(c for c in fake.calls if c["method"] == "thread/start")["params"]
        self.assertTrue(start["ephemeral"])
        self.assertEqual(start["sandbox"], "read-only")
        self.assertEqual(start["dynamicTools"], [])
        turn = next(c for c in fake.calls if c["method"] == "turn/start")["params"]
        self.assertEqual(turn["outputSchema"], OUTPUT_SCHEMA)
        self.assertEqual(turn["effort"], "medium")
        self.assertEqual(turn["input"][1:], [
            {"type": "skill", "name": "character", "path": "/home/character/chat/skills/character/SKILL.md"},
            {"type": "skill", "name": "character-memory", "path": "/home/character/chat/skills/character-memory/SKILL.md"},
        ])
        self.assertFalse(any(c["method"] == "thread/resume" for c in fake.calls))

    def test_budget_interrupts_before_reply(self):
        fake = FakeRpc()
        with self.assertRaises(RuntimeBudgetExceeded) as raised:
            self.run_fake(fake, token_budget=400)
        self.assertEqual(raised.exception.usage_tokens, 421)
        self.assertTrue(any(c["method"] == "turn/interrupt" for c in fake.calls))

    def test_timeout_preserves_latest_observed_usage_and_still_interrupts(self):
        fake = FakeRpc(events=[
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "t-1", "turnId": "u-1",
                "tokenUsage": {"total": {"totalTokens": 300}}}},
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "t-1", "turnId": "u-1",
                "tokenUsage": {"total": {"totalTokens": 525}}}},
            TimeoutError("fake timeout")])
        with self.assertRaises(TimeoutError) as raised:
            self.run_fake(fake)
        self.assertEqual(raised.exception.usage_tokens, 525)
        self.assertTrue(any(c["method"] == "turn/interrupt" for c in fake.calls))
        self.assertTrue(fake.closed)

    def test_engine_accounts_failed_cumulative_tool_usage_without_delivering(self):
        fake = FakeRpc(events=[
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "t-1", "turnId": "u-1",
                "tokenUsage": {"total": {"totalTokens": 6257}}}},
            {"id": 901, "method": "item/tool/call", "params": {"threadId": "t-1", "turnId": "u-1",
                "tool": "memory_search", "arguments": {"query": "你好"}}},
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "t-1", "turnId": "u-1",
                "tokenUsage": {"total": {"totalTokens": 12540}}}},
        ])
        snapshot = self.engine_failure_snapshot(fake)
        self.assertEqual(snapshot["reservation"], {"tokens": 12540, "actual": 12540})
        self.assertEqual(snapshot["account_calls"], 1)
        self.assertEqual(snapshot["event"]["status"], "failed")
        self.assertEqual(snapshot["event"]["error"], "RuntimeBudgetExceeded")
        self.assertEqual(snapshot["outcome"]["usage_tokens"], 12540)
        self.assertEqual(snapshot["outcome"]["budget_accounting"], "observed_usage_recorded")
        self.assertEqual(snapshot["outbox"], [])
        self.assertTrue(fake.tool_result["success"])
        self.assertTrue(any(c["method"] == "turn/interrupt" for c in fake.calls))
        self.assertTrue(fake.closed)

    def test_unknown_failed_usage_keeps_reservation_without_booking_zero(self):
        fake = FakeRpc(events=[RuntimeFailure("fake failure before usage notification")])
        snapshot = self.engine_failure_snapshot(fake)
        self.assertEqual(snapshot["reservation"]["tokens"], 8000)
        self.assertEqual(snapshot["account_calls"], 0)
        self.assertIsNone(snapshot["outcome"]["usage_tokens"])
        self.assertEqual(snapshot["outcome"]["budget_accounting"], "reservation_retained_usage_unknown")
        self.assertEqual(snapshot["outbox"], [])
        self.assertTrue(any(c["method"] == "turn/interrupt" for c in fake.calls))

    def engine_failure_snapshot(self, fake):
        cfg = json.loads((Path(__file__).resolve().parents[1] / "config/example.json").read_text(encoding="utf-8"))
        cfg["runtime"] = config()
        cfg["budgets"]["turn_tokens"] = 8000
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "state.db")
            try:
                now = time.time()
                store.ingest("usage-event", "qq:private:alice", "message", "你好？", "alice", now, now)
                with patch("qq_character.runtime.verify_isolation", return_value={"user": "10001", "container_id": "verified-container"}), \
                     patch("qq_character.runtime.JsonRpcProcess", return_value=fake), \
                     patch.object(store, "account", wraps=store.account) as account:
                    Engine(cfg, store, Runtime(cfg["runtime"]), clock=lambda: now).step()
                return {
                    "reservation": store.rows("SELECT tokens,actual FROM reservations")[0],
                    "event": store.rows("SELECT status,error FROM events WHERE id='usage-event'")[0],
                    "outcome": json.loads(store.rows("SELECT payload FROM evidence WHERE kind='outcome'")[0]["payload"]),
                    "outbox": store.rows("SELECT * FROM outbox"), "account_calls": account.call_count,
                }
            finally:
                store.close()

    def test_timeout_interrupts_and_closes(self):
        fake = FakeRpc(events=[TimeoutError("fake timeout")])
        with self.assertRaises(TimeoutError):
            self.run_fake(fake)
        self.assertTrue(fake.closed)
        self.assertTrue(any(c["method"] == "turn/interrupt" for c in fake.calls))

    def test_failed_or_interrupted_turn_never_delivers(self):
        for status, exception in [("failed", RuntimeFailure), ("interrupted", RuntimeInterrupted)]:
            with self.subTest(status=status):
                fake = FakeRpc(events=[{"method": "turn/completed", "params": {
                    "threadId": "t-1", "turn": {"id": "u-1", "status": status}}}])
                with self.assertRaises(exception):
                    self.run_fake(fake)

    def test_only_scoped_dynamic_tool_can_run(self):
        fake = FakeRpc()
        fake.events.insert(0, {"id": 900, "method": "item/tool/call", "params": {
            "tool": "source_read", "arguments": {"source_id": "approved"}, "threadId": "t-1", "turnId": "u-1"}})
        seen = []
        self.run_fake(fake, tool_handler=lambda name, args: seen.append((name, args)) or {"evidence_id": "source-1"})
        self.assertEqual(seen, [("source_read", {"source_id": "approved"})])
        self.assertTrue(fake.tool_result["success"])

    def test_cross_thread_tool_and_extra_scope_argument_rejected(self):
        for tid, args in [("other-thread", {"query": "secret"}), ("t-1", {"query": "secret", "scope": "other"})]:
            fake = FakeRpc()
            fake.events.insert(0, {"id": 900, "method": "item/tool/call", "params": {
                "tool": "memory_search", "arguments": args, "threadId": tid, "turnId": "u-1"}})
            seen = []
            self.run_fake(fake, tool_handler=lambda *args: seen.append(args))
            self.assertEqual(seen, [])
            self.assertFalse(fake.tool_result["success"])

    def test_unknown_and_approval_requests_are_denied(self):
        rpc = object.__new__(JsonRpcProcess)
        rpc.request_handler = None
        sent = []
        rpc.send = sent.append
        for method in ["item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                       "item/permissions/requestApproval", "mcpServer/elicitation/request", "tool/requestUserInput"]:
            rpc._server_request({"id": 9, "method": method, "params": {}})
        self.assertEqual(sent[0]["result"]["decision"], "decline")
        self.assertEqual(sent[2]["result"]["permissions"], {})
        self.assertIn("error", sent[-1])

    def test_unverified_home_never_starts_model(self):
        with patch("qq_character.runtime.JsonRpcProcess") as process:
            with self.assertRaises(IsolationError):
                Runtime({"home": "new-home", "cwd": "new-dir", "isolated": True}).run("hi", scope="x")
            process.assert_not_called()

    def test_development_cannot_reuse_chat_environment(self):
        cfg = config()
        cfg["development"] = config()
        with self.assertRaises(IsolationError):
            Runtime(cfg).run("write a skill", scope="x", mode="development")

    def test_development_injects_only_its_separately_audited_skills(self):
        cfg = config()
        dev = config()
        dev.update(home="/home/character/dev", cwd="/workspace/dev",
                   instruction_roots=["/home/character/dev", "/workspace/dev"],
                   allowed_skills=["approved-development"])
        dev["isolation"]["container"] = "character-dev-test"
        cfg["development"] = dev
        data = discoveries()
        data["config/read"]["layers"] = []
        data["skills/list"]["data"][0]["skills"] = [{"name": "approved-development", "enabled": True,
            "path": "/home/character/dev/skills/approved-development/SKILL.md", "scope": "user"}]
        fake = FakeRpc()
        original_request = fake.request
        def request(method, params, deadline):
            if method == "initialize":
                return {"userAgent": "fake-wire-only", "codexHome": dev["home"]}
            if method in data:
                return data[method]
            return original_request(method, params, deadline)
        fake.request = request
        with patch("qq_character.runtime.verify_isolation", return_value={"user": "10001", "container_id": "verified-container"}), \
             patch("qq_character.runtime.JsonRpcProcess", return_value=fake):
            Runtime(cfg).run("review a tool", scope="development:approved", mode="development")
        turn = next(c for c in fake.calls if c["method"] == "turn/start")["params"]
        self.assertEqual(turn["input"][1:], [{"type": "skill", "name": "approved-development",
            "path": "/home/character/dev/skills/approved-development/SKILL.md"}])

    def test_development_diagnostics_select_separate_profile(self):
        cfg = config()
        cfg["development"] = {**config(), "home": "/home/character/dev", "cwd": "/workspace/dev",
                              "isolation": {"container": "character-dev-test"}}
        with patch("qq_character.runtime.diagnose_runtime", return_value={"live_ready": False}) as diagnostic:
            Runtime(cfg).diagnose(mode="development")
            diagnostic.assert_called_once_with(cfg["development"], timeout_s=30, mode="development")
        cfg["development"]["isolation"]["container"] = cfg["isolation"]["container"]
        with self.assertRaises(IsolationError):
            Runtime(cfg).diagnose(mode="development")

    def test_wrong_json_types_never_reach_delivery(self):
        bad = output()
        bad["reply"] = {"internal_task_log": "secret"}
        with self.assertRaises(RuntimeFailure):
            _validate_output(bad)


class IsolationTests(unittest.TestCase):
    def test_container_has_watchdog_independent_of_docker_client_lifetime(self):
        proof = {"user": "10001", "container_id": "immutable-id"}
        command = launch_command(config(), proof, watchdog_seconds=80)
        index = command.index("immutable-id")
        self.assertEqual(command[index + 1:index + 6], ["timeout", "--signal=TERM", "--kill-after=5s", "80s", "codex"])
        with self.assertRaises(IsolationError):
            launch_command(config(), proof)

    def test_credentials_not_inherited(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
                "OPENAI_API_KEY": "DO-NOT-INHERIT", "CODEX_HOME": "normal-home", "GITHUB_TOKEN": "DO-NOT-INHERIT"}):
            env = minimal_environment({"home": tmp})
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertEqual(env["CODEX_HOME"], str(Path(tmp).resolve()))

    def test_irrelevant_development_skill_blocks_casual_chat(self):
        data = discoveries()
        data["skills/list"]["data"][0]["skills"].append({"name": "superpowers", "enabled": True,
                "path": "/home/character/chat/skills/superpowers/SKILL.md"})
        self.assertFalse(audit_discovery(config(), data, mode="chat")["ok"])

    def test_ordinary_chat_requires_both_character_and_memory_skills(self):
        data = discoveries()
        data["skills/list"]["data"][0]["skills"] = []
        self.assertFalse(audit_discovery(config(), data, mode="chat")["ok"])
        cfg = config()
        cfg["allowed_skills"].append("superpowers")
        self.assertFalse(audit_discovery(cfg, discoveries(), mode="chat")["ok"])

    def test_planning_and_user_input_utility_tools_must_be_disabled(self):
        for name in ["update_plan", "experimental_request_user_input"]:
            data = discoveries()
            data["config/read"]["config"]["tools"][name]["enabled"] = True
            self.assertFalse(audit_discovery(config(), data, mode="chat")["ok"])

    def test_alpha_tool_policy_uses_explicit_layer_evidence_without_claiming_inventory(self):
        data = discoveries()
        tools = data["config/read"]["config"].pop("tools")
        self.assertFalse(audit_discovery(config(), data, mode="chat")["ok"])
        data["config/read"]["layers"].insert(0, {"name": {"type": "sessionFlags"}, "config": {"tools": tools}})
        report = audit_discovery(config(), data, mode="chat")
        self.assertTrue(report["ok"])
        self.assertFalse(report["complete_tool_inventory_verified"])
        self.assertIn("sessionFlags", report["utility_tool_policy"]["update_plan"])

    def test_instruction_outside_approved_root_rejected(self):
        with self.assertRaises(IsolationError):
            check_thread_response(config(), {"approvalPolicy": "never", "sandbox": {"type": "readOnly"},
                "thread": {"ephemeral": True}, "instructionSources": ["/home/user/private/AGENTS.md"]})

    def test_valid_and_invalid_docker_proofs(self):
        policy = {"image_id": "sha256:approved", "allowed_volumes": [{"name": "character-home", "target": "/home/character"}]}
        info = {"Id": "container-1", "Image": "sha256:approved", "State": {"Running": True},
                "Config": {"User": "10001:10001", "Env": ["PATH=/usr/bin"]},
                "HostConfig": {"Privileged": False, "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"], "NetworkMode": "bridge"},
                "Mounts": [{"Type": "volume", "Name": "character-home", "Destination": "/home/character"}]}
        self.assertTrue(_check_container(info, policy)["verified"])
        with_tmp = copy.deepcopy(info)
        with_tmp["HostConfig"]["Tmpfs"] = {"/tmp": "rw,nosuid,nodev,noexec,size=64m"}
        with_tmp["Mounts"].append({"Type": "tmpfs", "Destination": "/tmp"})
        self.assertTrue(_check_container(with_tmp, policy)["verified"])
        for mutation in (lambda x: x["HostConfig"].update(Privileged=True),
                         lambda x: x["Config"].update(User="0"),
                         lambda x: x["Mounts"].append({"Type": "bind", "Source": "/home/user", "Destination": "/mnt/user"}),
                         lambda x: x["Config"].update(Env=["OPENAI_API_KEY=secret"])):
            bad = copy.deepcopy(info)
            mutation(bad)
            with self.assertRaises(IsolationError):
                _check_container(bad, policy)


if __name__ == "__main__":
    unittest.main()
