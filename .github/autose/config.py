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
# Each role has a chain: the first model, then the models tried in order when one is rate-limited, out of
# quota or unusable (no credit, bad key). Two splits hold for every chain:
# no code-writing agent (requirements excepted: design, implement, tester) ever uses a reviewer model, and
# the judge skips the model the requirements agent ran on (agents.llm_json avoid_model).
# Claude first, tiered by how much the role's output matters (Opus reviewer, Sonnet writers, Haiku judge);
# then free Gemini models; then free OpenRouter models. A provider that answers with a billing or auth error
# is skipped for the rest of the run.
A, G = "anthropic/", "gemini/"
OPUS, SONNET, HAIKU = A + "claude-opus-5-5", A + "claude-sonnet-5-5", A + "claude-haiku-5-5"
FALLBACK = os.environ.get("AUTOSE_FALLBACK_MODEL", "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free")
REVIEW_FALLBACK = os.environ.get("AUTOSE_REVIEW_FALLBACK_MODEL", "openrouter/google/gemma-4-31b-it:free")
MODELS = {
    "requirements": (SONNET, G + "gemini-3.5-flash-lite", G + "gemini-3.1-flash-lite", FALLBACK, G + "gemini-3.6-flash"),
    # Code-writing roles (design also runs Stage 3; tester). They never use a reviewer model.
    "design":       (SONNET, G + "gemini-3.5-flash", G + "gemini-3.8-flash", G + "gemini-3-flash-preview", FALLBACK,
                     G + "gemini-3.6-flash", G + "gemini-3.1-flash-lite", G + "gemini-3.7-flash", G + "gemma-4-26b-a4b-it"),
    "tester":       (SONNET, G + "gemini-3-flash-preview", G + "gemini-3.6-flash", G + "gemini-3.5-flash", FALLBACK,
                     G + "gemini-3.8-flash", G + "gemini-3.1-flash-lite", G + "gemini-3.7-flash", G + "gemma-4-26b-a4b-it"),
    "judge":        (HAIKU, G + "gemini-3.8-flash", G + "gemini-3.6-flash", G + "gemini-3.1-flash-lite",
                     G + "gemini-3.5-flash-lite"),
    # Reviewer fallbacks: only models no code-writing role uses (3.5-flash-lite is the requirements agent's).
    "reviewer":     (OPUS, G + "gemini-4-hydrogen", G + "gemma-4-31b-it", REVIEW_FALLBACK,
                     "openrouter/nvidia/nemotron-3.5-super-vl:free", "openrouter/inclusionai/ling-3.2-flash:free",
                     G + "gemini-3.5-flash-lite"),
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
STEP_LIMIT = {"requirements": 25, "design": 45, "implement": 90, "tester": 40, "reviewer": 40}
WALL_SECONDS = {"requirements": 600, "design": 900, "implement": 1500, "tester": 1200, "reviewer": 1200}
# Token caps bound the free models' loops (each step resends the whole history). Claude Code runs are bounded
# in dollars by BUDGET_USD instead.  TUNE
STAGE_TOKEN_CAP = 3_000_000      # input + output tokens per agent stage
RUN_TOKEN_CAP = 15_000_000       # per workflow run
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
