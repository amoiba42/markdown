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

# ---------- Models (ADR-011) ----------
# Each role: primary model and one same-family fallback that keeps both splits:
# no code-writing agent uses the reviewer's model; the judge never uses the requirements model.
G = "gemini/"
# ADR-015: the fallback is a model from another provider (OpenRouter), so a Gemini quota stop does not end the run.
# The reviewer keeps a Gemini fallback: the code-writing roles can fall back to FALLBACK, and the reviewer
# must never share a model with them. The judge avoids the requirements model at call time (agents.llm_json).
FALLBACK = os.environ.get("AUTOSE_FALLBACK_MODEL", "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free")
MODELS = {
    "requirements": (G + "gemini-3.5-flash-lite", FALLBACK),
    "design":       (G + "gemini-3.8-flash", FALLBACK),
    "tester":       (G + "gemini-3.6-flash", FALLBACK),
    "judge":        (G + "gemini-3.8-flash", FALLBACK),
    "reviewer":     (G + "gemini-4-hydrogen", G + "gemini-3.5-flash-lite"),
}
# Experiment 1, variant B (experiment/variants.md): reviewer and judge on Qwen via OpenRouter.
VARIANT = os.environ.get("AUTOSE_VARIANT", "A")
OPENROUTER_QWEN = os.environ.get("AUTOSE_QWEN_MODEL", "openrouter/qwen/qwen3-coder:free")  # TODO(me): pin ID
if VARIANT == "B":
    MODELS["judge"] = (OPENROUTER_QWEN, OPENROUTER_QWEN)
    MODELS["reviewer"] = (OPENROUTER_QWEN, OPENROUTER_QWEN)

# ---------- Caps (ADR-006); numbers are TUNE ----------
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
SANDBOX_PIP = ["pytest", "pyyaml", "ruff"]
