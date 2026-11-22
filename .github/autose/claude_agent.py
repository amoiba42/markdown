"""Agent runner on headless Claude Code (`claude -p`), with the same interface as agents.Agent.

- Claude Code runs inside the sandbox container (sandbox.py), so every tool call it makes (Bash, file reads
  and edits) runs there, with no secrets and no internet. It reaches the Anthropic API only through the
  key-injecting proxy (proxy.py); the sandbox holds a placeholder key.
- Caps: a dollar cap per invocation (--max-budget-usd), a wall-clock cap (timeout), and the run token cap
  shared with agents.py. A resume continues the same session with --resume.
- The transcript (stream-json events) is saved as the trajectory artifact.
"""

import json
from pathlib import Path

import sandbox
from agents import RUN_TOKENS
from common import log
from config import BUDGET_USD, CLAUDE_TOOLS, RUN_TOKEN_CAP, WALL_SECONDS, WS

CLAUDE_MODELS = {   # role -> (model, fallback or None); same tiering as config.MODELS (ADR-016)
    "requirements": ("claude-sonnet-5-5", None),        # Haiku is the judge's model; no fallback onto it
    "design": ("claude-sonnet-5-5", "claude-haiku-5-5"),
    "tester": ("claude-sonnet-5-5", "claude-haiku-5-5"),
    "reviewer": ("claude-opus-5-5", None),              # nothing a code-writer uses; no fallback
}
PROMPT_DIR = WS / ".autose"
FOOTER = """

## How you work
- Every tool runs in a sandbox with no network and no secrets. Do not try to reach GitHub, push, or read secrets.
- Use absolute paths. Write your outputs only to the paths named in the task.
- When the task is complete, stop and reply with one short line saying what you wrote.
"""


class _ModelInfo:                                   # orchestrator reads agent.model.config.model_name
    def __init__(self, name: str):
        self.config = type("Cfg", (), {"model_name": name})()


class ClaudeCodeAgent:
    def __init__(self, role: str, cap_key: str, system: str, cwd: str, trajectory: Path):
        self.role, self.cap_key, self.cwd, self.trajectory = role, cap_key, cwd, trajectory
        self.model_name, self.fallback = CLAUDE_MODELS[role]
        self.model = _ModelInfo("anthropic/" + self.model_name)
        self.session: str | None = None
        self.events: list[dict] = []
        self.tokens, self.steps, self.cost = 0, 0, 0.0
        PROMPT_DIR.mkdir(parents=True, exist_ok=True)
        self.system_file = PROMPT_DIR / f"{trajectory.stem}-system.md"
        self.system_file.write_text(system + FOOTER)

    # ---- interface shared with agents.Agent ----

    def start(self, task: str) -> dict:
        return self._run(task, self.cap_key)

    def resume(self, task: str, cap_key: str) -> dict:
        self.tokens = 0                             # caps and token counts are per invocation, as in agents.Agent
        return self._run(task, cap_key)

    def commands_run(self) -> list[str]:
        """Every Bash command this agent executed (used by the G6 evidence check)."""
        cmds = []
        for ev in self.events:
            if ev.get("type") != "assistant":
                continue
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    cmd = (block.get("input") or {}).get("command")
                    if cmd:
                        cmds.append(cmd)
        return cmds

    # ---- internals ----

    def _command(self, cap_key: str) -> str:
        q = sandbox.quote
        parts = ["claude", "-p", "--bare", "--output-format", "stream-json", "--verbose",
                 "--model", self.model_name, "--tools", CLAUDE_TOOLS, "--dangerously-skip-permissions",
                 "--add-dir", "/ws", "--max-budget-usd", str(BUDGET_USD[cap_key]),
                 "--append-system-prompt-file", sandbox.ws_path(self.system_file)]
        if self.fallback:
            parts += ["--fallback-model", self.fallback]
        if self.session:
            parts += ["--resume", self.session]
        return f"cd {q(self.cwd)} && " + " ".join(q(p) for p in parts)

    def _run(self, task: str, cap_key: str) -> dict:
        if RUN_TOKENS["total"] >= RUN_TOKEN_CAP:
            return self._result("TokenCapExceeded")
        timeout = WALL_SECONDS[cap_key]
        rc, out, err = sandbox.run_split(self._command(cap_key), timeout, sandbox.CLAUDE_ENV, task)
        result = None
        for line in out.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.events.append(ev)
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                self.session = ev.get("session_id") or self.session
                if ev.get("model"):
                    self.model.config.model_name = "anthropic/" + ev["model"]
            elif ev.get("type") == "assistant":
                self.steps += 1
            elif ev.get("type") == "result":
                result = ev
        self._save()
        if result is None:
            if rc == 124:
                return self._result("TimeExceeded")
            log(f"claude exited {rc} without a result: {err[-1500:]}")
            return self._result(f"Error: claude exited {rc}: {err[-500:].strip()}")
        self.session = result.get("session_id") or self.session
        usage = result.get("usage") or {}
        used = sum(int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                                     "cache_creation_input_tokens", "cache_read_input_tokens"))
        self.tokens += used
        RUN_TOKENS["total"] += used
        self.cost += float(result.get("total_cost_usd") or 0)
        sub = str(result.get("subtype") or "")
        if sub == "success" and not result.get("is_error"):
            return self._result("Submitted")
        if "budget" in sub or "max_turns" in sub:
            return self._result("LimitsExceeded")
        return self._result(f"Error: {sub or 'error'}: {str(result.get('result') or '')[:300]}")

    def _result(self, status: str) -> dict:
        return {"exit_status": status, "tokens": self.tokens, "model": self.model.config.model_name,
                "steps": self.steps, "cost_usd": round(self.cost, 4)}

    def _save(self) -> None:
        self.trajectory.parent.mkdir(parents=True, exist_ok=True)
        self.trajectory.write_text(json.dumps(self.events, indent=1)[:20_000_000])


def make_agent(role: str, cap_key: str, system: str, cwd: str, trajectory: Path):
    """Claude Code for roles on Claude models; mini-swe-agent otherwise (e.g. variant B's Qwen reviewer)."""
    from agents import Agent, claude_usable
    from config import HARNESS, MODELS
    if HARNESS == "claude-code" and role in CLAUDE_MODELS and MODELS[role][0].startswith("anthropic/") \
            and claude_usable():
        return ClaudeCodeAgent(role, cap_key, system, cwd, trajectory)
    return Agent(role, cap_key, system, cwd, trajectory)
