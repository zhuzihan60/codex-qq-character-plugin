"""Fail-closed runtime isolation checks. No credentials are read or returned."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any


class IsolationError(RuntimeError):
    pass


def minimal_environment(config: dict) -> dict[str, str]:
    """Do not inherit API keys, MCP configuration, or the caller's Codex state."""
    keep = ("PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "LANG")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    character_home = str(Path(config["home"]).resolve())
    os_home = str(Path(config.get("os_home", str(Path(character_home).parent / "os-home"))).resolve())
    env.update(CODEX_HOME=character_home, HOME=os_home, USERPROFILE=os_home,
               CODEX_SQLITE_HOME=character_home)
    return env


def _check_container(info: dict, policy: dict) -> dict:
    """Validate inspect evidence, not an operator's boolean claiming isolation."""
    errors: list[str] = []
    host, cfg, state = info.get("HostConfig", {}), info.get("Config", {}), info.get("State", {})
    if not state.get("Running"):
        errors.append("container is not running")
    user = str(cfg.get("User", ""))
    if not re.fullmatch(r"[1-9][0-9]*(?::[1-9][0-9]*)?", user):
        errors.append("container must run under an explicit non-root numeric UID")
    if not policy.get("image_id") or info.get("Image") != policy["image_id"]:
        errors.append("image digest is absent or differs from the approved image_id")
    if host.get("Privileged") or not host.get("ReadonlyRootfs"):
        errors.append("require non-privileged container with read-only root filesystem")
    if "ALL" not in [str(x).upper() for x in host.get("CapDrop", [])] or host.get("CapAdd"):
        errors.append("require cap_drop ALL and no added capabilities")
    if not any(str(x).replace(":", "=") == "no-new-privileges=true" or x == "no-new-privileges"
               for x in host.get("SecurityOpt", [])):
        errors.append("require no-new-privileges")
    if host.get("PidMode") or host.get("IpcMode") in ("host",) or host.get("NetworkMode") in ("host",):
        errors.append("host process/IPC/network namespaces are forbidden")
    if str(host.get("NetworkMode", "")).startswith("container:"):
        errors.append("sharing another container's network namespace is forbidden")
    if host.get("Devices") or host.get("VolumesFrom") or host.get("PortBindings"):
        errors.append("device mounts, inherited volumes and published ports are forbidden")
    allowed = {(str(v["name"]), str(v["target"])) for v in policy.get("allowed_volumes", [])}
    seen = set()
    tmpfs = host.get("Tmpfs") or {}
    for target, options in tmpfs.items():
        if target != "/tmp" or not {"noexec", "nosuid", "nodev"}.issubset(set(str(options).split(","))):
            errors.append("tmpfs is only allowed at /tmp with noexec,nosuid,nodev")
    for mount in info.get("Mounts", []):
        if mount.get("Type") == "tmpfs":
            if mount.get("Destination") != "/tmp" or "/tmp" not in tmpfs:
                errors.append("unapproved tmpfs mount")
            continue
        key = (str(mount.get("Name", "")), str(mount.get("Destination", "")))
        seen.add(key)
        if mount.get("Type") != "volume" or key not in allowed:
            errors.append("unapproved mount (only explicitly named dedicated volumes are supported)")
        if key[1] in ("/", "/etc", "/var/run", "/run") or "docker.sock" in key[1]:
            errors.append("system or Docker socket mount is forbidden")
    if seen != allowed:
        errors.append("actual volumes differ from the complete approved volume list")
    # Print names only; never put environment values in diagnostics.
    permitted_env = {"PATH", "HOME", "LANG", "LC_ALL", "TZ", "CODEX_HOME", "NODE_VERSION",
                     "YARN_VERSION", "PYTHON_VERSION", "PYTHON_SHA256", "PYTHONUNBUFFERED",
                     "PYTHONDONTWRITEBYTECODE", "QQ_CHARACTER_PROFILE"}
    for entry in cfg.get("Env", []):
        if entry.startswith("QQ_CHARACTER_PROFILE=") and entry.split("=", 1)[1] not in {"chat", "development"}:
            errors.append("unknown character bootstrap profile")
    unexpected_env = sorted({entry.split("=", 1)[0] for entry in cfg.get("Env", [])} - permitted_env)
    if unexpected_env:
        errors.append("unapproved container environment variable names: " + ", ".join(unexpected_env))
    if errors:
        raise IsolationError("; ".join(errors))
    return {"verified": True, "mechanism": "docker-inspect", "container_id": info.get("Id"),
            "image_id": info.get("Image"), "user": user, "mounts": sorted([list(x) for x in seen]),
            "limitations": ["Docker daemon and approved image are trusted computing base",
                            "named volumes must be created exclusively for this character"]}


def verify_isolation(config: dict) -> dict:
    """Only a fresh Docker inspect is accepted for live inference in this prototype."""
    policy = config.get("isolation", {})
    if policy.get("type") != "docker":
        raise IsolationError("Live inference blocked: a new CODEX_HOME/directory or isolated=true is not OS isolation. "
                             "This prototype requires a verified dedicated Docker container.")
    container = str(policy.get("container", ""))
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", container):
        raise IsolationError("invalid container name")
    try:
        proc = subprocess.run([config.get("docker_command", "docker"), "inspect", container],
                              capture_output=True, text=True, timeout=15, check=True)
        infos = json.loads(proc.stdout)
        if len(infos) != 1:
            raise ValueError("ambiguous container")
        proof = _check_container(infos[0], policy)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        raise IsolationError("Cannot verify Docker isolation: " + type(exc).__name__) from exc
    for key in ("cwd", "home"):
        path = PurePosixPath(config.get(key, ""))
        if not path.is_absolute() or ".." in path.parts or str(path) in ("/", "/root"):
            raise IsolationError(f"{key} must be a dedicated absolute container path")
    # A named volume can itself be a disguised host bind through driver options.
    # Reject that case and require the deployment's dedicated-volume label.
    for volume in policy.get("allowed_volumes", []):
        try:
            result = subprocess.run([config.get("docker_command", "docker"), "volume", "inspect", volume["name"]],
                                    capture_output=True, text=True, timeout=15, check=True)
            metadata = json.loads(result.stdout)[0]
            if (metadata.get("Driver") != "local" or metadata.get("Options")
                    or metadata.get("Labels", {}).get("qq-character.role") != "runtime"):
                raise IsolationError("volume must be dedicated, labeled, local and have no host-bind driver options")
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            raise IsolationError("Cannot verify dedicated volume metadata") from exc
    return proof


def launch_command(config: dict, proof: dict | None = None, *, watchdog_seconds: int | None = None) -> list[str]:
    command = config.get("command", ["codex", "app-server", "--listen", "stdio://"])
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise IsolationError("runtime.command must be a nonempty string array")
    if proof:
        if not isinstance(watchdog_seconds, int) or watchdog_seconds <= 0:
            raise IsolationError("container execution requires a positive watchdog deadline")
        return [config.get("docker_command", "docker"), "exec", "-i", "--user", proof["user"],
                "--workdir", config["cwd"], "--env", "CODEX_HOME=" + config["home"],
                "--env", "HOME=" + config.get("os_home", "/home/character"),
                proof["container_id"], "timeout", "--signal=TERM", "--kill-after=5s",
                f"{watchdog_seconds}s", *command]
    return list(command)


def local_path_inventory(config: dict) -> dict:
    """Read paths and existence only, never AGENTS/config/auth contents."""
    cwd, home = Path(config["cwd"]).resolve(), Path(config["home"]).resolve()
    candidates = {home / "AGENTS.md", home / "config.toml", home / "skills",
                  Path(config.get("os_home", home.parent / "os-home")) / ".agents" / "skills"}
    for parent in (cwd, *cwd.parents):
        candidates.update({parent / "AGENTS.md", parent / ".agents" / "skills", parent / ".codex" / "config.toml"})
    return {"paths_present": sorted(str(p) for p in candidates if p.exists()),
            "os_isolation_verified": False, "note": "path inventory alone is not an isolation proof"}


def _under(path: str, root: str) -> bool:
    # Runtime paths can be POSIX while the bridge runs on Windows.
    if path.startswith("/"):
        p, r = PurePosixPath(path), PurePosixPath(root)
        return ".." not in p.parts and (p == r or r in p.parents)
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def audit_discovery(config: dict, discoveries: dict[str, Any], *, mode: str) -> dict:
    """Allowlist effective capabilities. Unknown/error states fail closed."""
    errors, skills, plugins, sources = [], [], [], []
    allowed_roots = config.get("instruction_roots", [config["cwd"], config["home"]])
    allowed_skills = set(config.get("allowed_skills", ["character", "character-memory"]))
    character_skills = {"character", "character-memory"}
    if mode != "development" and allowed_skills != character_skills:
        errors.append("ordinary modes must allow only the character and character-memory skills")
    cfg_result = discoveries.get("config/read", {})
    cfg = cfg_result.get("config", {})
    disabled_utilities = ("update_plan", "experimental_request_user_input")
    utility_evidence = {}
    for tool in disabled_utilities:
        effective = (cfg.get("tools") or {}).get(tool, {}).get("enabled")
        if effective is not None:
            utility_evidence[tool] = "effective-config"
            if effective is not False:
                errors.append("utility tool must be disabled: " + tool)
            continue
        # The tested alpha's ConfigReadResponse.ToolsV2 exposes only web_search.
        # Audit the explicit returned session-layer input, and report this weaker
        # evidence honestly; do not mislabel it a model-visible tool inventory.
        flags = [layer for layer in cfg_result.get("layers") or []
                 if layer.get("name", {}).get("type") == "sessionFlags" and not layer.get("disabledReason")]
        if not flags or any((layer.get("config", {}).get("tools") or {}).get(tool, {}).get("enabled") is not False
                            for layer in flags):
            errors.append("missing explicit session-layer tool disable: " + tool)
        utility_evidence[tool] = "sessionFlags; effective typed RPC omits this field"
    if cfg.get("approval_policy") != "never" or cfg.get("sandbox_mode") != "read-only":
        errors.append("effective approval/sandbox does not equal never/read-only")
    features = cfg.get("features", {})
    for flag in ("shell_tool", "apps", "hooks", "multi_agent", "memories", "goals", "remote_plugin"):
        if features.get(flag) is not False:
            errors.append("effective feature must explicitly be false: " + flag)
    if cfg.get("web_search") != "disabled":
        errors.append("built-in web search must be disabled; use scoped source_read")
    if cfg.get("mcp_servers"):
        errors.append("configured MCP servers are not allowed in the minimal runtime")
    for layer in cfg_result.get("layers") or []:
        if layer.get("disabledReason"):
            continue
        name = layer.get("name", {})
        path = name.get("file") or name.get("dotCodexFolder")
        if path:
            sources.append(path)
            if not any(_under(path, root) for root in allowed_roots):
                errors.append("configuration layer outside approved instruction roots: " + path)
    entries = discoveries.get("skills/list", {}).get("data")
    if not isinstance(entries, list):
        errors.append("skills discovery missing")
    for entry in entries or []:
        if entry.get("errors"):
            errors.append("skill discovery returned errors")
        for skill in entry.get("skills", []):
            if not skill.get("enabled"):
                continue
            skills.append({k: skill.get(k) for k in ("name", "path", "scope")})
            if skill.get("name") not in allowed_skills or not any(
                    _under(skill.get("path", ""), root) for root in allowed_roots):
                errors.append("unapproved enabled skill: " + str(skill.get("name")))
    discovered_names = {skill["name"] for skill in skills}
    if len(discovered_names) != len(skills):
        errors.append("ambiguous duplicate enabled skill names")
    if mode != "development" and not character_skills.issubset(discovered_names):
        errors.append("required character and memory skills are not both discovered")
    if mode == "development" and (not allowed_skills or not allowed_skills.issubset(discovered_names)):
        errors.append("approved development skills are missing from effective discovery")
    mcp = discoveries.get("mcpServerStatus/list", {})
    if "data" not in mcp or mcp.get("data") or mcp.get("nextCursor"):
        errors.append("MCP discovery must be complete and empty")
    catalog = discoveries.get("plugin/list", {})
    if "marketplaces" not in catalog or catalog.get("marketplaceLoadErrors"):
        errors.append("plugin diagnostic incomplete")
    for market in catalog.get("marketplaces", []):
        for plugin in market.get("plugins", []):
            if plugin.get("enabled") and plugin.get("installed"):
                plugins.append(plugin.get("id"))
                errors.append("installed enabled plugin is not approved in this minimal runtime")
    return {"ok": not errors, "errors": errors, "skills": skills, "plugins": plugins,
            "configuration_sources": sources, "mode": mode,
            "utility_tool_policy": utility_evidence, "complete_tool_inventory_verified": False,
            "note": "plugin/list is experimental diagnostic surface, pinned to tested CLI version"}


def check_thread_response(config: dict, response: dict) -> None:
    if response.get("approvalPolicy") != "never":
        raise IsolationError("thread approval policy differs from never")
    sandbox = response.get("sandbox", {})
    if sandbox.get("type") != "readOnly" or sandbox.get("networkAccess", False):
        raise IsolationError("thread sandbox is not read-only without command networking")
    if not response.get("thread", {}).get("ephemeral"):
        raise IsolationError("thread is not ephemeral")
    if "instructionSources" not in response:
        raise IsolationError("thread did not report instructionSources")
    for root in response.get("runtimeWorkspaceRoots", []):
        if not _under(root, config["cwd"]):
            raise IsolationError("thread contains an unexpected workspace root")
    for path in response.get("instructionSources", []):
        if not any(_under(path, root) for root in config.get("instruction_roots", [config["cwd"], config["home"]])):
            raise IsolationError("thread loaded instruction outside approved roots: " + path)
