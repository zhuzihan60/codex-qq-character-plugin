"""Receive-only real OneBot authentication check, before starting the bridge."""
import asyncio
import contextlib
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import re
import shutil
import subprocess

from qq_character.config import load
from qq_character.service import ProcessLock
from qq_character.store import Store
from qq_character.transport import OneBotTransport


async def check(config, store):
    transport = OneBotTransport(config, store)
    task = asyncio.create_task(transport.run())
    waiter = asyncio.create_task(transport.connected.wait())
    try:
        done, _ = await asyncio.wait({task, waiter}, timeout=45, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            task.result()
        return waiter in done and transport.connected.is_set()
    finally:
        for child in (waiter, task):
            child.cancel()
        for child in (waiter, task):
            with contextlib.suppress(asyncio.CancelledError):
                await child


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    cfg = load(root / "config/local.json")
    # DPAPI decrypted in a local operator process; capture directly, never log it.
    secret_path = str(root / ".state/secrets/onebot.dpapi").replace("'", "''")
    script = ("$ErrorActionPreference='Stop';$s=[IO.File]::ReadAllText('" + secret_path +
              "')|ConvertTo-SecureString;$p=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s);"
              "try{[Console]::Write([Runtime.InteropServices.Marshal]::PtrToStringBSTR($p))}"
              "finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p);$s.Dispose()}")
    secret_env = dict(os.environ)
    secret_shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not secret_shell:
        raise RuntimeError("PowerShell is required for the Windows operator preflight")
    if Path(secret_shell).name.casefold() == "powershell.exe":
        secret_env = {k: v for k, v in secret_env.items() if k.casefold() != "psmodulepath"}
    secret = subprocess.run([secret_shell, "-NoLogo", "-NoProfile", "-NonInteractive",
                             "-ExecutionPolicy", "Bypass", "-Command", script],
                            capture_output=True, text=True, timeout=10, env=secret_env)
    if secret.returncode:
        codes = re.findall(r"FullyQualifiedErrorId\s*:\s*([A-Za-z0-9_.,]+)", secret.stderr)
        raise RuntimeError("Operator secret helper failed: " + (",".join(codes) or "unclassified") + "; no secret displayed")
    os.environ[cfg["transport"]["token_env"]] = secret.stdout
    secret = None
    with ProcessLock(Path(cfg["database"]).with_suffix(".lock")):
        store = Store(cfg["database"])
        try:
            passed = asyncio.run(check(cfg, store))
        finally:
            store.close()
            os.environ.pop(cfg["transport"]["token_env"], None)
    result = {"verification": "REAL_ONEBOT_AUTHENTICATED_CONNECTION",
              "checked_at": datetime.now(timezone.utc).isoformat(), "passed": passed,
              "bot_id": cfg["transport"]["bot_id"], "messages_sent": 0,
              "checks": ["path", "bearer_token", "X-Self-ID", "Universal"]}
    (root / ".state/onebot-preflight.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
