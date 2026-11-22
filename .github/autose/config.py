"""Static configuration: models (ADR-011), caps (ADR-006), paths and protected files (ADR-007).

Numbers marked TUNE are starting values, not decided in any ADR. Change them here only.
"""

import os
from pathlib import Path

# ---------- Paths ----------
TOOL_DIR = Path(__file__).resolve().parent            # .github/autose in the trusted checkout
PROMPTS = TOOL_DIR / "prompts"
TMP = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "autose"
WS = TMP / "ws"            # mounted into the sandbox as /ws (workspaces only, no secrets)
ART = TMP / "art"          # artifacts bundle, never mounted into the sandbox
SANDBOX_WS = "/ws"         # path of WS inside the sandbox container

# ---------- Base commit of the fork (Python-Markdown) ----------
BASE_REF = os.environ.get("AUTOSE_BASE_REF", "master")

# ---------- Models (ADR-011, ADR-016) ----------
# Each role: (primary, fallback). Two splits must hold for every pair:
# no code-writing agent (design, implement, tester) uses the reviewer's model, and the judge never uses the
# model the requirements agent ran on (the judge avoids it at call time, agents.llm_json).
# Primaries are Claude, tiered by how much the role's output matters: the reviewer on Opus, the agents that
# write the spec, design, code and tests on Sonnet, the testability judge (a yes/no per criterion) on Haiku.
# Fallbacks come from other providers, so a Claude rate limit does not end the run and no code-writer
# can fall back onto the reviewer's model.
A, G = "anthropic/", "gemini/"
OPUS, SONNET, HAIKU = A + "claude-opus-5-5", A + "claude-sonnet-5-5", A + "claude-haiku-5-5"
FALLBACK = os.environ.get("AUTOSE_FALLBACK_MODEL", "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free")
MODELS = {
    "requirements": (SONNET, HAIKU),
    "design":       (SONNET, FALLBACK),          # also runs Stage 3 (implementation)
    "tester":       (SONNET, FALLBACK),
    "judge":        (HAIKU, G + "gemini-3.8-flash"),
    "reviewer":     (OPUS, G + "gemini-4-hydrogen"),
}
# Experiment 1, variant B (experiment/variants.md): reviewer and judge on Qwen via OpenRouter.
VARIANT = os.environ.get("AUTOSE_VARIANT", "A")
OPENROUTER_QWEN = os.environ.get("AUTOSE_QWEN_MODEL", "openrouter/qwen/qwen3-coder:free")  # TODO(me): pin ID
if VARIANT == "B":
    MODELS["judge"] = (OPENROUTER_QWEN, OPENROUTER_QWEN)
    MODELS["reviewer"] = (OPENROUTER_QWEN, OPENROUTER_QWEN)

# ---------- Caps (ADR-006); numbers are TUNE ----------
MAX_STAGE_REASKS = 1             # ADR-017: re-asks of the same agent after its own check (G1, G2, G6) fails
STAGE4_TESTER_RETRIES = 1        # ADR-017: Stage 4 failures caused by the tester's own files go back to the tester
STEP_LIMIT = {"requirements": 25, "design": 30, "implement": 60, "tester": 40, "reviewer": 40}
WALL_SECONDS = {"requirements": 600, "design": 900, "implement": 1500, "tester": 1200, "reviewer": 1200}
STAGE_TOKEN_CAP = 1_500_000      # input + output tokens per agent stage
RUN_TOKEN_CAP = 6_000_000        # per workflow run
COMMAND_TIMEOUT = 300            # seconds per bash command inside the sandbox

# ---------- Retry caps (ADR-006); experiment 2 overrides via AUTOSE_RETRIES ----------
STAGE3_RETRIES = int(os.environ.get("AUTOSE_RETRIES", "2"))
STAGE2_REDOS = 1 if STAGE3_RETRIES > 0 else 0
MAX_REVIEW_ROUNDS = 2            # fixed by the brief

# ---------- Checks (ADR-007) ----------
MAX_DESIGN_TARGETS = 10
PROTECTED_PREFIXES = (".github/", ".git/")
PROTECTED_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "pytest.ini", "MANIFEST.in",
    ".flake8", "ruff.toml", ".ruff.toml", ".coveragerc", ".pre-commit-config.yaml",
    "conftest.py", "ARCHITECTURE.md", "requirements.txt",
}
PROTECTED_SUFFIXES = (".lock", ".cfg", ".ini", ".toml")
TEST_DIR = "tests/"
CI_POLL_SECONDS = 600

# ---------- Sandbox (ADR-010: agents and repo code never share the runner's environment) ----------
SANDBOX_IMAGE = "python:3.12-bookworm"
# setuptools and wheel let the offline sandbox build the package with --no-build-isolation.
SANDBOX_PIP = ["pytest", "pyyaml", "ruff", "setuptools>=77", "wheel"]

# ---------- Agent harness ----------
# claude-code: agents on Claude models run as headless Claude Code inside the sandbox, behind the proxy.
# mini: mini-swe-agent through LiteLLM (also used for any non-Claude model, e.g. variant B's Qwen reviewer).
HARNESS = os.environ.get("AUTOSE_HARNESS", "claude-code")
CLAUDE_CODE_VERSION = "2.1.330"
CLAUDE_TOOLS = "Bash,Read,Write,Edit,Glob,Grep"          # no web tools; the sandbox has no internet anyway
# Dollar cap per agent invocation (Claude Code --max-budget-usd); a resume gets its own cap.  TUNE
BUDGET_USD = {"requirements": 1.5, "design": 2.0, "implement": 4.0, "tester": 2.5, "reviewer": 4.0}
