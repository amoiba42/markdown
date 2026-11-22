"""Job 2: Release (Stage 5). A script, no agent (ADR-003).

- Regex secret scan on the diff (ADR-006, ADR-008).
- Push with the fine-grained PAT (contents + pull requests, no workflow permission); never force-push.
- Open or reuse the pull request with a minimal pr.md (ADR-002).
- G5: the pull request is open and CI passes, polled for up to 10 minutes (ADR-007).

Outputs: status = ready | stop, pr = <number>, base = <commit the branch started from>
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

from common import ART, ISSUE, REPO, RUN_URL, gh_api, load_state, log, redact, set_output, write_explanation
from config import BASE_REF, CI_POLL_SECONDS

CHECKOUT = Path(os.environ.get("AUTOSE_CHECKOUT", "work"))
SECRET_PATTERNS = {
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,}",
    "Anthropic key": r"sk-ant-[A-Za-z0-9_\-]{20,}",
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
    # The auth header is never part of an error message: only the return code and git's stderr are kept.
    res = subprocess.run(["git", "-c", header, "push", "-q", "origin", f"HEAD:refs/heads/{branch}"],
                         cwd=CHECKOUT, capture_output=True, text=True)
    if res.returncode:
        raise Stop(f"git push to {branch} failed ({res.returncode}): {redact(res.stderr)[-1500:]}")
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
                fields={"title": f"AutoSE: issue #{ISSUE}", "head": state["branch"], "base": BASE_REF, "body": body})
    return pr["number"]


def wait_for_ci(sha: str) -> tuple[bool, str]:
    """All check runs on the commit completed and passed. The push run and the pull_request run register at
    different times, so a result counts only after a minute has passed and the set of runs is unchanged
    across two polls."""
    start = time.time()
    deadline, seen = start + CI_POLL_SECONDS, None
    while time.time() < deadline:
        runs = gh_api(f"/repos/{REPO}/commits/{sha}/check-runs").get("check_runs", [])
        ids = sorted(r["id"] for r in runs)
        settled = runs and all(r["status"] == "completed" for r in runs)
        if settled and ids == seen and time.time() - start >= 60:
            bad = [f"{r['name']}: {r['conclusion']}" for r in runs
                   if r["conclusion"] not in ("success", "skipped", "neutral")]
            return (not bad), "; ".join(bad) or f"{len(runs)} checks passed"
        seen = ids if settled else None
        time.sleep(20)
    return False, f"CI did not finish within {CI_POLL_SECONDS}s"


def branch_base() -> str:
    """The commit on the base branch the AutoSE branch started from. The reviewer diffs against it, so later
    commits on the base branch (for example tooling updates) do not show up as reversed changes."""
    return git("merge-base", f"origin/{BASE_REF}", "HEAD")


def main() -> None:
    state = load_state()
    try:
        patch = ART / "diff.patch"
        found = secret_scan(patch.read_text())
        if found:
            raise Stop(f"secret scan matched: {found}")
        changed = sorted({l[6:] for l in patch.read_text().splitlines() if l.startswith("+++ b/")})
        sha = push(state, patch)
        set_output("base", branch_base())
        pr = open_pr(state, changed)
        state["branch_head"], state["pr"] = sha, pr
        ok, msg = wait_for_ci(sha)
        (ART / "release.json").write_text(json.dumps({"pr": pr, "sha": sha, "ci": msg}, indent=2))
        if not ok:
            raise Stop(f"G5 failed: {msg}")
        set_output("pr", str(pr))
        set_output("status", "ready")
    except (Stop, RuntimeError) as e:
        write_explanation("Stage 5", "release stopped", str(e))
    except subprocess.CalledProcessError as e:     # str(e) would include the command line; keep only stderr
        write_explanation("Stage 5", "release stopped", f"a command failed ({e.returncode}): "
                          f"{(e.stderr or b'')[-1500:]!r}")
        set_output("status", "stop")
    except Exception as e:                          # never crash without an explanation (ADR-006)
        write_explanation("Stage 5", f"internal error: {type(e).__name__}", traceback.format_exc())
        set_output("status", "stop")
    finally:
        (ART / "release_state.json").write_text(json.dumps(
            {"branch_head": state.get("branch_head"), "pr": state.get("pr")}, indent=2))
        log("release done")


if __name__ == "__main__":
    sys.exit(main())
