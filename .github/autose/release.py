"""Job 2: Release (Stage 5). A script, no agent (ADR-003).

- Regex secret scan on the diff (ADR-006, ADR-008).
- Push with the fine-grained PAT (contents + pull requests, no workflow permission); never force-push.
- Open or reuse the pull request with a minimal pr.md (ADR-002).
- G5: the pull request is open and CI passes, polled for up to 10 minutes (ADR-007).

Outputs: status = ready | stop, pr = <number>
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from common import ART, ISSUE, REPO, RUN_URL, gh_api, load_state, log, set_output, write_explanation
from config import CI_POLL_SECONDS

CHECKOUT = Path(os.environ.get("AUTOSE_CHECKOUT", "work"))
SECRET_PATTERNS = {
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,}",
    "Google API key": r"AIza[0-9A-Za-z_\-]{35}",
    "OpenRouter key": r"sk-or-[A-Za-z0-9\-]{20,}",
    "OpenAI-style key": r"sk-[A-Za-z0-9]{32,}",
    "AWS access key": r"AKIA[0-9A-Z]{16}",
    "Slack token": r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    "Private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}


class Stop(Exception):
    pass


def git(*args: str, check=True) -> str:
    res = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=CHECKOUT, text=True,
                         capture_output=True)
    if check and res.returncode:
        raise Stop(f"git {' '.join(args)} failed: {res.stderr[-1500:]}")
    return res.stdout.strip()


def secret_scan(patch: str) -> list[str]:
    added = "\n".join(l[1:] for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++"))
    return [name for name, rx in SECRET_PATTERNS.items() if re.search(rx, added)]


def push(state: dict, patch: Path) -> str:
    auth = base64.b64encode(f"x-access-token:{os.environ['AUTOSE_PAT']}".encode()).decode()
    header = f"http.extraheader=AUTHORIZATION: basic {auth}"
    branch = state["branch"]
    remote = subprocess.run(["git", "ls-remote", "origin", f"refs/heads/{branch}"], cwd=CHECKOUT,
                            text=True, capture_output=True).stdout.split()
    if remote and remote[0] != state["branch_head"]:
        raise Stop(f"{branch} has commits AutoSE did not make ({remote[0]} != {state['branch_head']}); "
                   "Release never force-pushes")
    if remote:
        git("fetch", "-q", "origin", branch)
    git("checkout", "-q", "-B", branch, state["branch_head"])
    if patch.stat().st_size:
        git("apply", "--index", "--binary", str(patch))
    git("-c", "user.name=autose-bot", "-c", "user.email=autose-bot@users.noreply.github.com",
        "commit", "-q", "--allow-empty", "-m", f"autose: issue #{ISSUE}, round {state['review_round'] + 1}")
    subprocess.run(["git", "-c", header, "push", "-q", "origin", f"HEAD:refs/heads/{branch}"],
                   cwd=CHECKOUT, check=True, capture_output=True)
    return git("rev-parse", "HEAD")


def open_pr(state: dict, changed: list[str]) -> int:
    owner = REPO.split("/")[0]
    prs = gh_api(f"/repos/{REPO}/pulls?head={owner}:{state['branch']}&state=all")
    if prs and prs[0]["state"] == "closed":
        raise Stop(f"PR #{prs[0]['number']} was closed by a human; not reopening")
    body = (f"Closes #{ISSUE}\n\n- Branch: `{state['branch']}`\n- Changed files:\n"
            + "\n".join(f"  - `{f}`" for f in changed) + f"\n- Actions run: {RUN_URL}\n")
    (ART / "pr.md").write_text(body)
    if prs:
        return prs[0]["number"]
    pr = gh_api(f"/repos/{REPO}/pulls", method="POST", token_env="AUTOSE_PAT",
                fields={"title": f"AutoSE: issue #{ISSUE}", "head": state["branch"], "base": "main", "body": body})
    return pr["number"]


def wait_for_ci(sha: str) -> tuple[bool, str]:
    deadline = time.time() + CI_POLL_SECONDS
    while time.time() < deadline:
        runs = gh_api(f"/repos/{REPO}/commits/{sha}/check-runs").get("check_runs", [])
        if runs and all(r["status"] == "completed" for r in runs):
            bad = [f"{r['name']}: {r['conclusion']}" for r in runs
                   if r["conclusion"] not in ("success", "skipped", "neutral")]
            return (not bad), "; ".join(bad) or f"{len(runs)} checks passed"
        time.sleep(20)
    return False, f"CI did not finish within {CI_POLL_SECONDS}s"


def main() -> None:
    state = load_state()
    try:
        patch = ART / "diff.patch"
        found = secret_scan(patch.read_text())
        if found:
            raise Stop(f"secret scan matched: {found}")
        changed = sorted({l[6:] for l in patch.read_text().splitlines() if l.startswith("+++ b/")})
        sha = push(state, patch)
        pr = open_pr(state, changed)
        state["branch_head"], state["pr"] = sha, pr
        ok, msg = wait_for_ci(sha)
        (ART / "release.json").write_text(json.dumps({"pr": pr, "sha": sha, "ci": msg}, indent=2))
        if not ok:
            raise Stop(f"G5 failed: {msg}")
        set_output("pr", str(pr))
        set_output("status", "ready")
    except (Stop, subprocess.CalledProcessError, RuntimeError) as e:
        write_explanation("Stage 5", "release stopped", str(e))
        set_output("status", "stop")
    finally:
        (ART / "release_state.json").write_text(json.dumps(
            {"branch_head": state.get("branch_head"), "pr": state.get("pr")}, indent=2))
        log("release done")


if __name__ == "__main__":
    sys.exit(main())
