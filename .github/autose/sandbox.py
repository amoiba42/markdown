"""Sandbox container: every agent bash command and every step that runs repository code runs here.

Why a container and not only a stripped environment: a process on the runner can read its parent's
environment through /proc/<pid>/environ, so stripping variables is not enough to keep the model key
out of reach (ADR-006, ADR-010). The container has its own process namespace and no secrets.
"""

import os
import shlex
import subprocess

from config import COMMAND_TIMEOUT, SANDBOX_IMAGE, SANDBOX_PIP, SANDBOX_WS, WS

NAME = "autose-sandbox"
SAFE_ENV = {"HOME": "/tmp/home", "LANG": "C.UTF-8", "PAGER": "cat", "GIT_PAGER": "cat", "TERM": "dumb",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1"}


UID_GID = f"{os.getuid()}:{os.getgid()}"
PATH = "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"


def start() -> None:
    """Container setup runs as root once; every later command runs as the runner's own user, so files
    written into the mounted workspaces stay editable by the orchestrator."""
    WS.mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", NAME, "--env-file", "/dev/null",
                    "-v", f"{WS}:{SANDBOX_WS}:z", "-w", SANDBOX_WS, SANDBOX_IMAGE, "sleep", "infinity"],
                   check=True, capture_output=True)
    setup = (f"python -m venv /opt/venv && /opt/venv/bin/pip install -q {' '.join(SANDBOX_PIP)} "
             f"&& mkdir -p /tmp/home && chown -R {UID_GID} /opt/venv /tmp/home")
    subprocess.run(["docker", "exec", NAME, "bash", "-lc", setup], check=True, capture_output=True, timeout=600)
    run("git config --global --add safe.directory '*'")


def run(command: str, cwd: str = SANDBOX_WS, timeout: int = COMMAND_TIMEOUT, extra_env: dict | None = None):
    """Run a shell command inside the sandbox as the runner's user. Returns (returncode, combined output)."""
    env_args = []
    for k, v in (SAFE_ENV | {"PATH": PATH} | (extra_env or {})).items():
        env_args += ["-e", f"{k}={v}"]
    cmd = ["docker", "exec", "--user", UID_GID, *env_args, "-w", cwd, NAME,
           "timeout", "--kill-after=5", str(timeout), "bash", "-c", command]
    try:
        res = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout + 30)
    except subprocess.TimeoutExpired:
        return -1, f"[timed out after {timeout}s]"
    out = res.stdout + res.stderr
    if res.returncode == 124:
        out += f"\n[timed out after {timeout}s]"
    return res.returncode, out


def ws_path(local) -> str:
    """Map a host path under WS to its path inside the sandbox."""
    return SANDBOX_WS + "/" + str(local.relative_to(WS)).replace("\\", "/") if local != WS else SANDBOX_WS


def stop() -> None:
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)


def quote(s: str) -> str:
    return shlex.quote(s)
