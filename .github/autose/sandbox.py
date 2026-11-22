"""Sandbox container: every agent tool call and every step that runs repository code runs here.

Why a container and not only a stripped environment: a process on the runner can read its parent's
environment through /proc/<pid>/environ, so stripping variables is not enough to keep secrets out of
reach (ADR-006, ADR-010). The container has its own process namespace and no secrets.

Network: setup (Python tools, Claude Code, the first git fetch) runs with internet. lockdown() then moves
the container onto an internal Docker network whose only other member is the key-injecting proxy
(proxy.py), so agents can reach the Anthropic API through the proxy and nothing else.
"""

import os
import shlex
import subprocess

from config import (CLAUDE_CODE_VERSION, COMMAND_TIMEOUT, SANDBOX_IMAGE, SANDBOX_PIP, SANDBOX_WS, TOOL_DIR, WS)

NAME = "autose-sandbox"
PROXY = "autose-proxy"
NET = "autose-internal"
PROXY_URL = f"http://{PROXY}:8080"
SAFE_ENV = {"HOME": "/tmp/home", "LANG": "C.UTF-8", "PAGER": "cat", "GIT_PAGER": "cat", "TERM": "dumb",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1"}
# Claude Code inside the sandbox: a placeholder key (the proxy adds the real one) and no other traffic.
CLAUDE_ENV = {"ANTHROPIC_BASE_URL": PROXY_URL, "ANTHROPIC_API_KEY": "placeholder-proxy-adds-key",
              "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
              "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "IS_SANDBOX": "1"}

UID_GID = f"{os.getuid()}:{os.getgid()}"
PATH = "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"


def _docker(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True, timeout=timeout)


def start() -> None:
    """Container setup runs as root once; every later command runs as the runner's own user, so files
    written into the mounted workspaces stay editable by the orchestrator."""
    WS.mkdir(parents=True, exist_ok=True)
    stop()
    _docker("network", "create", "--internal", NET)
    # The proxy holds the key, sits on the internal network and on the default bridge (internet).
    _docker("run", "-d", "--name", PROXY, "--network", NET, "-e", "ANTHROPIC_API_KEY",
            "-v", f"{TOOL_DIR / 'proxy.py'}:/proxy.py:ro,z", "python:3.12-slim", "python", "-u", "/proxy.py")
    _docker("network", "connect", "bridge", PROXY)
    _docker("run", "-d", "--name", NAME, "--env-file", "/dev/null",
            "-v", f"{WS}:{SANDBOX_WS}:z", "-w", SANDBOX_WS, SANDBOX_IMAGE, "sleep", "infinity")
    setup = (f"python -m venv /opt/venv && /opt/venv/bin/pip install -q {' '.join(shlex.quote(p) for p in SANDBOX_PIP)} "
             f"&& apt-get -qq update && apt-get -qq install -y --no-install-recommends nodejs npm >/dev/null "
             f"&& npm install -g --silent @anthropic-ai/claude-code@{CLAUDE_CODE_VERSION} "
             f"&& mkdir -p /tmp/home && chown -R {UID_GID} /opt/venv /tmp/home")
    res = subprocess.run(["docker", "exec", NAME, "bash", "-lc", setup], capture_output=True, text=True,
                         timeout=900)
    if res.returncode:
        raise RuntimeError(f"sandbox setup failed: {(res.stdout + res.stderr)[-2000:]}")
    run("git config --global --add safe.directory '*'")


def lockdown() -> None:
    """Cut the sandbox off the internet; keep only the path to the proxy. Call after setup and the first fetch."""
    _docker("network", "disconnect", "bridge", NAME)
    _docker("network", "connect", NET, NAME)
    rc, out = run("python -c \"import urllib.request as u; u.urlopen('https://pypi.org', timeout=5)\"", timeout=20)
    if rc == 0:
        raise RuntimeError("sandbox still reaches the internet after lockdown")


def run(command: str, cwd: str = SANDBOX_WS, timeout: int = COMMAND_TIMEOUT, extra_env: dict | None = None,
        input_text: str | None = None):
    """Run a shell command inside the sandbox as the runner's user. Returns (returncode, combined output)."""
    env_args = []
    for k, v in (SAFE_ENV | {"PATH": PATH} | (extra_env or {})).items():
        env_args += ["-e", f"{k}={v}"]
    cmd = ["docker", "exec", *(["-i"] if input_text is not None else []), "--user", UID_GID, *env_args,
           "-w", cwd, NAME, "timeout", "--kill-after=5", str(timeout), "bash", "-c", command]
    try:
        res = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout + 30, input=input_text)
    except subprocess.TimeoutExpired:
        return -1, f"[timed out after {timeout}s]"
    out = res.stdout + res.stderr
    if res.returncode == 124:
        out += f"\n[timed out after {timeout}s]"
    return res.returncode, out


def run_split(command: str, timeout: int, extra_env: dict, input_text: str) -> tuple[int, str, str]:
    """Like run(), but stdout and stderr apart (Claude Code's stream-json is on stdout)."""
    env_args = []
    for k, v in (SAFE_ENV | {"PATH": PATH} | extra_env).items():
        env_args += ["-e", f"{k}={v}"]
    cmd = ["docker", "exec", "-i", "--user", UID_GID, *env_args, "-w", SANDBOX_WS, NAME,
           "timeout", "--kill-after=10", str(timeout), "bash", "-c", command]
    try:
        res = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout + 60, input=input_text)
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), ""
    return res.returncode, res.stdout, res.stderr


def ws_path(local) -> str:
    """Map a host path under WS to its path inside the sandbox."""
    return SANDBOX_WS + "/" + str(local.relative_to(WS)).replace("\\", "/") if local != WS else SANDBOX_WS


def stop() -> None:
    for name in (NAME, PROXY):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "network", "rm", NET], capture_output=True)


def quote(s: str) -> str:
    return shlex.quote(s)
