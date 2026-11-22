"""Shared helpers: GitHub calls through the gh CLI, state, artifacts, issue mirror.

Event fields reach this code only through environment variables (ADR-006).
"""

import base64
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from config import ART

REPO = os.environ["GITHUB_REPOSITORY"]
ISSUE = int(os.environ["ISSUE_NUMBER"])
RUN_ID = os.environ.get("GITHUB_RUN_ID", "local")
RUN_URL = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{REPO}/actions/runs/{RUN_ID}"
BUNDLE = f"autose-issue-{ISSUE}"
MIRROR_MARK = "<!-- autose-mirror -->"
STOP_MARK = "<!-- autose-stop -->"
ASSUMPTIONS_MARK = "<!-- autose-assumptions -->"
DONE_MARK = "<!-- autose-done -->"

# Strings that look like credentials. Anything posted to a public issue or uploaded goes through redact().
SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                              # Google API key
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),                               # OpenAI / OpenRouter style
    re.compile(r"(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)(basic\s+)[A-Za-z0-9+/=]{16,}"),                    # HTTP basic auth (e.g. git extraheader)
    re.compile(r"(?i)((?:api[_-]?key|key|token|secret|password)[\"']?\s*[=:]\s*[\"']?)[A-Za-z0-9._\-]{12,}"),
]


def log(msg: str) -> None:
    print(f"[autose {datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def sh(cmd: list[str], *, cwd=None, env=None, check=True, input_text=None) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, input=input_text)
    if check and res.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({res.returncode}): {res.stderr[-2000:]}")
    return res


def gh_api(path: str, *, method="GET", fields: dict | None = None, token_env="GH_TOKEN") -> dict | list:
    env = os.environ | {"GH_TOKEN": os.environ[token_env]}
    cmd = ["gh", "api", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
    body = json.dumps(fields) if fields is not None else None
    if body is not None:
        cmd += ["--input", "-"]
    out = sh(cmd, env=env, input_text=body).stdout
    return json.loads(out) if out.strip() else {}


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")
    log(f"output {name}={value}")


# ---------- State (ADR-004) ----------

def new_state() -> dict:
    return {
        "issue": ISSUE, "snapshot": None, "branch": f"autose/issue-{ISSUE}", "branch_head": None,
        "current_stage": 0, "last_passed": 0, "target_stage": None, "review_round": 0,
        "retries": {"stage3": 0, "stage2": 0}, "run_ids": [], "done": False,
        "models_used": {}, "tokens": {}, "tester_tests": [], "status": "new",
    }


def load_state() -> dict:
    p = ART / "state.json"
    return json.loads(p.read_text()) if p.exists() else new_state()


def save_state(state: dict) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "state.json").write_text(json.dumps(state, indent=2))


def download_latest_bundle() -> bool:
    """Download the newest autose-issue-<n> artifact from earlier runs into ART. Needs actions: read."""
    data = gh_api(f"/repos/{REPO}/actions/artifacts?name={BUNDLE}&per_page=20")
    arts = [a for a in data.get("artifacts", []) if not a["expired"] and str(a["workflow_run"]["id"]) != RUN_ID]
    if not arts:
        return False
    newest = max(arts, key=lambda a: a["created_at"])
    raw = subprocess.run(
        ["gh", "api", f"/repos/{REPO}/actions/artifacts/{newest['id']}/zip"],
        env=os.environ, capture_output=True, check=True).stdout
    ART.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(raw)).extractall(ART)
    log(f"restored bundle from run {newest['workflow_run']['id']}")
    return True


# ---------- Issue comments ----------

def redact(text: str) -> str:
    """Replace credential-shaped strings, and the values of known secret variables, with [redacted]."""
    names = ["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GH_TOKEN", "AUTOSE_PAT"]
    for name in names + [f"GEMINI_API_KEY_{i}" for i in range(2, 10)]:
        val = os.environ.get(name, "")
        if len(val) >= 8:
            text = text.replace(val, "[redacted]")
            for form in (f"x-access-token:{val}", val):    # also the base64 form used in auth headers
                b64 = base64.b64encode(form.encode()).decode()
                text = text.replace(b64, "[redacted]").replace(b64.rstrip("="), "[redacted]")
    for pat in SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) if m.lastindex else "") + "[redacted]", text)
    return text


def short_error(text: str, limit: int = 400) -> str:
    """One readable line for a public comment: the provider's message when there is one, never the raw payload."""
    text = redact(text)
    m = re.search(r'"message":\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        line = m.group(1).replace("\\n", " ")
    else:   # explanation.md without a provider payload: keep the reason and the detail, drop headings and links
        keep = [l.strip() for l in text.splitlines()
                if l.strip() and not l.startswith(("#", "```", "- Stage:", "- Run:"))]
        line = " ".join(l.removeprefix("- Reason:").strip() for l in keep)
    line = re.sub(r"\s+", " ", line.split("For more information")[0]).strip()
    return line[:limit] or "no details"


def comment(body: str) -> None:
    gh_api(f"/repos/{REPO}/issues/{ISSUE}/comments", method="POST", fields={"body": redact(body)})


def _marked(mark: str) -> list[dict]:
    """Comments on the issue that start with `mark`, across all pages."""
    found, page = [], 1
    while True:
        batch = gh_api(f"/repos/{REPO}/issues/{ISSUE}/comments?per_page=100&page={page}")
        found += [c for c in batch if c["body"].startswith(mark)]
        if len(batch) < 100:
            return found
        page += 1


def upsert_comment(mark: str, body: str) -> None:
    """Keep one comment per kind on the issue: edit the one that starts with `mark`, else create it."""
    body = redact(f"{mark}\n{body}")
    mine = _marked(mark)
    if mine:
        gh_api(f"/repos/{REPO}/issues/comments/{mine[0]['id']}", method="PATCH", fields={"body": body})
    else:
        gh_api(f"/repos/{REPO}/issues/{ISSUE}/comments", method="POST", fields={"body": body})


def delete_comment(mark: str) -> None:
    """Remove the comment of one kind, e.g. a stop comment once a later run went past that stop."""
    try:
        for c in _marked(mark):
            gh_api(f"/repos/{REPO}/issues/comments/{c['id']}", method="DELETE")
    except RuntimeError as e:                       # cosmetic; never fail a job on it
        log(f"could not delete {mark} comment: {e}")


def update_mirror(state: dict, note: str = "") -> None:
    """One comment on the issue, edited in place, for humans only. The workflow never reads it."""
    shown = {k: state.get(k) for k in ("current_stage", "last_passed", "target_stage", "review_round",
                                       "done", "status", "branch", "run_ids", "tokens")}
    body = (f"**AutoSE status** ({note})\n\n```json\n{json.dumps(shown, indent=2)}\n```\n"
            f"Latest run: {RUN_URL}")
    upsert_comment(MIRROR_MARK, body)


def write_explanation(stage: str, reason: str, detail: str = "") -> None:
    """Explanation artifact for a stop (ADR-006)."""
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "explanation.md").write_text(
        redact(f"# Run stopped\n\n- Stage: {stage}\n- Reason: {reason}\n- Run: {RUN_URL}\n\n```\n{detail[-6000:]}\n```\n"))


def fail(msg: str) -> None:
    log(msg)
    sys.exit(1)


def read(p: Path) -> str:
    return p.read_text() if p.exists() else ""
