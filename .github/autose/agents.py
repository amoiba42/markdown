"""Agent runner on top of mini-swe-agent (ADR-010) and single LLM calls (judge).

- Bash is the only tool, and every command runs in the sandbox container (sandbox.py).
- Tool-call and wall-clock caps use mini-swe-agent's step and wall-clock limits.
- Token caps are counted here from each response's `usage` field (the cost limit stays at $0 on free models).
- On a transient error (429 or 503), the call waits and retries with backoff on the same model, then falls back
  once to the same-family fallback and retries again; only then does the stage stop.
"""

import copy
import json
import os
import random
import re
import time
from pathlib import Path

import litellm
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import FormatError, InterruptAgentFlow, LimitsExceeded
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.models.utils.cache_control import _clear_cache_control

import sandbox
from common import log
from config import MODELS, RUN_TOKEN_CAP, STAGE_TOKEN_CAP, STEP_LIMIT, WALL_SECONDS

RUN_TOKENS = {"total": 0}
TRANSIENT = (litellm.exceptions.RateLimitError, litellm.exceptions.ServiceUnavailableError,
             litellm.exceptions.InternalServerError,   # 429, 503, and 500/529 overloaded
             litellm.exceptions.APIConnectionError, litellm.exceptions.Timeout)   # dropped or slow connection
RETRIES = 3                   # retries per call on the same model before the fallback
MAX_WAIT = 60                 # seconds; longest single wait
MAX_TOKENS = 16000            # per response; litellm's default is too small for a thinking model's turn
MAX_OUTPUT = 10_000           # characters of one command's output kept in the conversation
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


def _wait_for(e: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying `e`, or None when waiting cannot help (daily quota exhausted)."""
    text = str(e)
    if re.search(r"PerDay|per day|per-day|_daily", text):     # Gemini and OpenRouter daily-limit wording
        return None
    m = re.search(r"retry in ([\d.]+)(ms|s)\b", text) or re.search(r'"retryDelay":\s*"([\d.]+)(s)"', text)
    base = float(m.group(1)) / (1000 if m.group(2) == "ms" else 1) if m else 0.0
    base = max(base, 5.0 * 2 ** attempt)                    # server hint, but at least exponential backoff
    return min(MAX_WAIT, base) * random.uniform(1.0, 1.5)   # jitter so parallel runs drift apart


def _retry(call, on_wait=None, fast_429=False):
    """Run call(); on a transient error wait (server hint, exponential, jitter) and retry. Re-raise at once on a
    daily quota, or after RETRIES retries, so the caller can switch to the fallback model.
    `fast_429`: re-raise any rate limit at once, because another API key for the same model is next.
    `on_wait(seconds)` is told about every wait, so the caller can keep it out of its wall-clock cap."""
    for attempt in range(RETRIES):
        try:
            return call()
        except TRANSIENT as e:
            if fast_429 and isinstance(e, litellm.exceptions.RateLimitError):
                raise
            wait = _wait_for(e, attempt)
            if wait is None:
                log(f"{type(e).__name__}: daily quota; not retrying")
                raise
            log(f"{type(e).__name__}; retrying in {wait:.0f}s")
            time.sleep(wait)
            if on_wait:
                on_wait(wait)
    return call()


# Several Gemini keys (repository secrets GEMINI_API_KEY, GEMINI_API_KEY_2 ... GEMINI_API_KEY_9) are used in
# turn. A chain entry ("slot") for a Gemini model with several keys is "<model>#<key index>"; a rejected or
# rate-limited key moves the call to the next key before the next model.
GEMINI_KEYS = [v for v in [os.environ.get("GEMINI_API_KEY")] +
               [os.environ.get(f"GEMINI_API_KEY_{i}") for i in range(2, 10)] if v]


def base(slot: str) -> str:
    """Model name of a chain slot, without the key index."""
    return slot.rsplit("#", 1)[0]


def slots(model: str) -> list[str]:
    if model.startswith("gemini/") and len(GEMINI_KEYS) > 1:
        return [f"{model}#{i}" for i in range(len(GEMINI_KEYS))]
    return [model]


def model_kwargs(slot: str) -> dict:
    if slot.startswith("anthropic/"):
        return {"max_tokens": MAX_TOKENS}
    if "#" in slot:
        return {"api_key": GEMINI_KEYS[int(slot.rsplit("#", 1)[1])]}
    return {}


DEAD: set[str] = set()      # providers (or one Gemini key) that answered with a billing or auth error
EXHAUSTED: set[str] = set() # slots whose daily quota ran out in this process


def provider(slot: str) -> str:
    """'anthropic', 'openrouter', 'gemini', or 'gemini#2' for one key of several."""
    name = slot.split("/", 1)[0]
    return name + "#" + slot.rsplit("#", 1)[1] if "#" in slot else name


def usable(slot: str) -> bool:
    return provider(slot) not in DEAD and slot not in EXHAUSTED


def next_slots(seq: list[str], slot: str) -> list[str]:
    return [m for m in seq[seq.index(slot) + 1:] if usable(m)]


def provider_dead(e: Exception) -> bool:
    """A provider that cannot serve this run at all: bad key, no access, or no credit."""
    text = str(e).lower()
    if isinstance(e, TRANSIENT):                 # rate limits mention "billing details"; they are not fatal
        return False
    return isinstance(e, (litellm.exceptions.AuthenticationError, litellm.exceptions.PermissionDeniedError)) \
        or "credit balance" in text or "insufficient credits" in text or "requires more credits" in text


def switchable(e: Exception) -> bool:
    """Errors after which the next model in the chain should be tried."""
    return isinstance(e, TRANSIENT) or isinstance(e, litellm.exceptions.NotFoundError) or provider_dead(e)


def mark_unusable(model: str, e: Exception) -> None:
    if provider_dead(e):
        DEAD.add(provider(model))
    elif isinstance(e, litellm.exceptions.RateLimitError) and _wait_for(e, 0) is None:
        EXHAUSTED.add(model)


def chain(role: str, avoid: str | None = None) -> list[str]:
    """The role's models in order, each Gemini model expanded into one slot per key, unusable ones dropped."""
    models = [m for m in MODELS[role] if m != avoid] or list(MODELS[role])
    expanded = [s for m in models for s in slots(m)]
    return [s for s in expanded if usable(s)] or expanded


def claude_usable() -> bool:
    """One tiny request to see whether the Anthropic key works (credit, auth). Cached per process."""
    if "anthropic" in DEAD:
        return False
    if getattr(claude_usable, "ok", None) is not None:
        return claude_usable.ok
    try:
        litellm.completion(model=MODELS["judge"][0], max_tokens=1, messages=[{"role": "user", "content": "ok"}])
        claude_usable.ok = True
    except Exception as e:
        claude_usable.ok = not provider_dead(e)
        if not claude_usable.ok:
            DEAD.add("anthropic")
            log(f"Anthropic API unusable, using the other models: {str(e)[:200]}")
    return claude_usable.ok


class CappedModel(LitellmModel):
    abort_exceptions = LitellmModel.abort_exceptions + list(TRANSIENT) + [litellm.exceptions.BadRequestError]

    def __init__(self, role: str, **kwargs):
        self.chain = chain(role)
        super().__init__(model_name=base(self.chain[0]), cost_tracking="ignore_errors", **kwargs)
        self.tokens = 0
        self.agent = None                       # set by Agent; waits are kept out of its wall-clock cap
        self._use(self.chain[0])

    def _use(self, slot: str) -> None:
        """Switch model (and key); prompt-cache markers and max_tokens only for Claude models."""
        self.slot = slot
        self.config.model_name = base(slot)
        self.config.model_kwargs = model_kwargs(slot)
        self.config.set_cache_control = "default_end" if slot.startswith("anthropic/") else None

    def _waited(self, seconds: float) -> None:
        if self.agent is not None:
            self.agent._start_time += seconds

    def _query(self, messages, **kwargs):
        while True:
            slot = self.slot
            msgs = messages
            if not slot.startswith("anthropic/"):
                msgs = copy.deepcopy(messages)
                for m in msgs:
                    _clear_cache_control(m)     # Claude prompt-cache markers mean nothing to other providers
            rest = next_slots(self.chain, slot)
            same_model_next = bool(rest) and base(rest[0]) == base(slot)
            try:
                return _retry(lambda: LitellmModel._query(self, msgs, **kwargs), self._waited, same_model_next)
            except Exception as e:
                if not switchable(e):
                    raise
                mark_unusable(slot, e)
                rest = next_slots(self.chain, slot)
                if not rest:
                    raise
                log(f"{type(e).__name__} on {slot}; switching to {rest[0]}")
                self._use(rest[0])

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
        if len(out) > MAX_OUTPUT:      # every output is resent on each later step; keep head and tail only
            cut = len(out) - MAX_OUTPUT
            out = (out[:MAX_OUTPUT * 2 // 3] + f"\n[... {cut} characters cut; use grep -n or sed -n to read a part ...]\n"
                   + out[-MAX_OUTPUT // 3:])
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
        self.model.agent = self.agent

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
    """Single LLM call that must return JSON. One re-ask on a parse error (ADR-006). Returns (data, tokens, model).
    Walks the role's model chain on rate limits and unusable providers."""
    models = chain(role, avoid_model)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    tokens, slot = 0, models[0]

    def call():
        nonlocal slot
        while True:
            rest = next_slots(models, slot)
            try:
                return _retry(lambda: litellm.completion(model=base(slot), messages=msgs, **model_kwargs(slot)),
                              fast_429=bool(rest) and base(rest[0]) == base(slot))
            except Exception as e:
                if not switchable(e):
                    raise
                mark_unusable(slot, e)
                rest = next_slots(models, slot)
                if not rest:
                    raise
                log(f"{type(e).__name__} on {slot}; switching to {rest[0]}")
                slot = rest[0]

    for attempt in range(2):
        resp = call()
        tokens += _usage(resp.model_dump())
        text = resp.choices[0].message.content or ""
        try:
            data = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
            RUN_TOKENS["total"] += tokens
            return data, tokens, base(slot)
        except json.JSONDecodeError:
            msgs += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "That was not valid JSON. Reply with the JSON only."}]
    RUN_TOKENS["total"] += tokens
    raise ValueError("judge output is not valid JSON after one re-ask")
