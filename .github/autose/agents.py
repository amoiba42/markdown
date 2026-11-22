"""Agent runner on top of mini-swe-agent (ADR-010) and single LLM calls (judge).

- Bash is the only tool, and every command runs in the sandbox container (sandbox.py).
- Tool-call and wall-clock caps use mini-swe-agent's step and wall-clock limits.
- Token caps are counted here from each response's `usage` field (the cost limit stays at $0 on free models).
- On a rate-limit error, the model falls back once to its same-family fallback; a second 429 stops the stage.
"""

import json
import time
from pathlib import Path

import litellm
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import FormatError, InterruptAgentFlow, LimitsExceeded
from minisweagent.models.litellm_model import LitellmModel

import sandbox
from common import log
from config import MODELS, RUN_TOKEN_CAP, STAGE_TOKEN_CAP, STEP_LIMIT, WALL_SECONDS

RUN_TOKENS = {"total": 0}
FINISH = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

SYSTEM_FOOTER = f"""

## How you work
- You have one tool: bash. Each command runs in a fresh shell in a sandbox; `cd` does not persist, so use absolute paths or `cd dir && cmd`.
- Do not try to reach GitHub, push, or read secrets. There are none in the sandbox.
- Write your outputs only to the paths named in the task.
- When you are completely done, run exactly: `{FINISH}`
"""


def _usage(response: dict) -> int:
    u = (response or {}).get("usage") or {}
    return int(u.get("prompt_tokens") or 0) + int(u.get("completion_tokens") or 0)


class CappedModel(LitellmModel):
    abort_exceptions = LitellmModel.abort_exceptions + [litellm.exceptions.RateLimitError]

    def __init__(self, role: str, **kwargs):
        primary, fallback = MODELS[role]
        super().__init__(model_name=primary, cost_tracking="ignore_errors", **kwargs)
        self.fallback, self.switched, self.tokens = fallback, False, 0

    def _query(self, messages, **kwargs):
        try:
            return super()._query(messages, **kwargs)
        except litellm.exceptions.RateLimitError:
            if self.switched or self.fallback == self.config.model_name:
                raise
            log(f"429 on {self.config.model_name}; falling back once to {self.fallback}")
            self.switched, self.config.model_name = True, self.fallback
            return super()._query(messages, **kwargs)

    def query(self, messages, **kwargs):
        if self.tokens >= STAGE_TOKEN_CAP or RUN_TOKENS["total"] >= RUN_TOKEN_CAP:
            raise LimitsExceeded({"role": "exit", "content": "TokenCapExceeded",
                                  "extra": {"exit_status": "TokenCapExceeded", "submission": ""}})
        message = super().query(messages, **kwargs)
        used = _usage(message.get("extra", {}).get("response"))
        self.tokens += used
        RUN_TOKENS["total"] += used
        return message


class SandboxEnvironment(LocalEnvironment):
    """Same interface as LocalEnvironment, but commands run in the sandbox with no secrets."""

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        rc, out = sandbox.run(action.get("command", ""), cwd=cwd or self.config.cwd or sandbox.SANDBOX_WS,
                              timeout=timeout or self.config.timeout)
        output = {"output": out, "returncode": rc, "exception_info": ""}
        self._check_finished(output)
        return output

    def get_template_vars(self, **kwargs) -> dict:
        return self.config.model_dump() | kwargs        # never expose os.environ to templates


def _raw(text: str) -> str:
    return "{% raw %}" + text + "{% endraw %}"


class Agent:
    """One mini-swe-agent conversation. `start` runs a first task; `resume` continues the same conversation."""

    def __init__(self, role: str, cap_key: str, system: str, cwd: str, trajectory: Path):
        self.role, self.cap_key = role, cap_key
        self.model = CappedModel(role)
        self.agent = DefaultAgent(
            self.model, SandboxEnvironment(cwd=cwd, timeout=300),
            system_template=_raw(system + SYSTEM_FOOTER), instance_template="{{ task }}",
            step_limit=STEP_LIMIT[cap_key], cost_limit=0.0,
            wall_time_limit_seconds=WALL_SECONDS[cap_key], output_path=trajectory)

    def start(self, task: str) -> dict:
        try:
            info = self.agent.run(task)
        except Exception as e:                      # e.g. second 429, auth error
            info = {"exit_status": f"Error: {type(e).__name__}: {e}"}
        return self._result(info)

    def resume(self, task: str, cap_key: str) -> dict:
        a = self.agent
        if a.messages and a.messages[-1].get("role") == "exit":
            a.messages.pop()
        a.config.step_limit, a.config.wall_time_limit_seconds = STEP_LIMIT[cap_key], WALL_SECONDS[cap_key]
        a.n_calls, a._start_time, self.model.tokens = 0, time.time(), 0
        a.add_messages(self.model.format_message(role="user", content=task))
        try:
            info = self._loop()
        except Exception as e:
            info = {"exit_status": f"Error: {type(e).__name__}: {e}"}
        return self._result(info)

    def _loop(self) -> dict:
        a = self.agent
        while True:
            try:
                a.step()
                a.n_consecutive_format_errors = 0
            except FormatError as e:
                a.n_consecutive_format_errors += 1
                a.add_messages(*e.messages)
                if a.n_consecutive_format_errors >= 3:
                    a.add_messages({"role": "exit", "content": "RepeatedFormatError",
                                    "extra": {"exit_status": "RepeatedFormatError", "submission": ""}})
            except InterruptAgentFlow as e:
                a.add_messages(*e.messages)
            finally:
                a.save(a.config.output_path)
            if a.messages[-1].get("role") == "exit":
                return a.messages[-1].get("extra", {})

    def _result(self, info: dict) -> dict:
        return {"exit_status": info.get("exit_status", ""), "tokens": self.model.tokens,
                "model": self.model.config.model_name, "steps": self.agent.n_calls}

    def commands_run(self) -> list[str]:
        """Every bash command this agent actually executed (used by the G6 evidence check)."""
        cmds = []
        for m in self.agent.messages:
            for act in (m.get("extra") or {}).get("actions", []) or []:
                if act.get("command"):
                    cmds.append(act["command"])
        return cmds


def llm_json(role: str, system: str, user: str, avoid_model: str | None = None) -> tuple[dict | list, int, str]:
    """Single LLM call that must return JSON. One re-ask on a parse error (ADR-006). Returns (data, tokens, model)."""
    primary, fallback = MODELS[role]
    models = [m for m in (primary, fallback) if m != avoid_model] or [fallback]
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    tokens, model, switched = 0, models[0], False
    for attempt in range(2):
        try:
            resp = litellm.completion(model=model, messages=msgs)
        except litellm.exceptions.RateLimitError:
            if switched or len(models) < 2:
                raise
            switched, model = True, models[1]
            resp = litellm.completion(model=model, messages=msgs)
        tokens += _usage(resp.model_dump())
        text = resp.choices[0].message.content or ""
        try:
            data = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
            RUN_TOKENS["total"] += tokens
            return data, tokens, model
        except json.JSONDecodeError:
            msgs += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "That was not valid JSON. Reply with the JSON only."}]
    RUN_TOKENS["total"] += tokens
    raise ValueError("judge output is not valid JSON after one re-ask")
