"""Job 3: independent review (Stage 6, ADR-002).

The job has contents: read and pull-requests: write only, no actions: read. It downloads only the
review-input bundle (issue snapshot + baseline.json), never the state bundle with spec, design,
test_report.md, attempt summaries or trajectories. Tests run in the sandbox with no secrets, then the
reviewer agent works in the same sandbox on the repository at the PR's latest commit.

Outputs: status = ready | stop, verdict = approve | changes_requested
"""

import json
import os
import secrets
import shutil
import sys
import traceback
from pathlib import Path
from string import Template

import gates
import sandbox
import schemas
from agents import Agent
from common import ART, ISSUE, REPO, gh_api, log, set_output, write_explanation
from config import MAX_STAGE_REASKS, PROMPTS, WS

PR_CHECKOUT = Path(os.environ.get("AUTOSE_PR_CHECKOUT", "pr"))
INPUT = Path(os.environ.get("AUTOSE_REVIEW_INPUT", "review_input"))
BASE_SHA = os.environ["AUTOSE_BASE_SHA"]
PR = int(os.environ["PR_NUMBER"])
OUT = WS / "out"
LABELS = {"approve": "autose-approved", "changes_requested": "autose-changes-requested"}


class Stop(Exception):
    pass


def render(review: dict) -> str:
    lines = [f"## AutoSE independent review: **{review['verdict']}**", ""]
    for ev in review["approval_evidence"]:
        lines.append(f"- evidence: `{ev.get('file', '')}:{ev.get('line', '')}`" if "file" in ev
                     else f"- evidence: `{ev['command']}`")
    for f in review["findings"]:
        ev = f["evidence"]
        where = f"`{ev['file']}:{ev['line']}`" if "file" in ev else f"`{ev['command']}`"
        lines.append(f"- **{f['id']}** [{f['classification']}, {f['severity']}] {f['explanation']} ({where})")
    lines.append("\n_This is an automated review. A human decides whether to merge._")
    return "\n".join(lines)


def main() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    try:
        issue = json.loads((INPUT / "issue.json").read_text())
        baseline = json.loads((INPUT / "baseline.json").read_text())
        WS.mkdir(parents=True, exist_ok=True)
        OUT.mkdir(parents=True, exist_ok=True)
        pr_ws = WS / "pr"
        if pr_ws.exists():
            shutil.rmtree(pr_ws)
        shutil.copytree(PR_CHECKOUT, pr_ws, symlinks=True)
        sandbox.start()

        # 1. Tests run first, in the sandbox, with no secrets (ADR-002).
        _, passed, failed, out = gates.run_pytest(pr_ws, "review")
        new_failures = sorted(failed - set(baseline["failed"]))
        test_summary = f"passed: {len(passed)}, failed: {len(failed)}, new failures vs baseline: {new_failures[:30]}"
        rc, diff = sandbox.run(f"git diff {BASE_SHA} HEAD", cwd="/ws/pr")
        if rc != 0 or not diff.strip():
            raise Stop(f"could not diff the pull request against its base {BASE_SHA}: {diff[-500:]}")

        # 2. Reviewer agent: issue snapshot, diff, repo at PR HEAD, test results. Nothing else.
        tag = f"untrusted-{secrets.token_hex(6)}"
        task = Template((PROMPTS / "reviewer.md").read_text()).safe_substitute(
            issue=f"<{tag}>\nTITLE: {issue['title']}\n\n{issue['body']}\n</{tag}>", tag=tag,
            diff=f"<{tag}>\n{diff[:60000]}\n</{tag}>", tests=test_summary)
        agent = Agent("reviewer", "reviewer", (PROMPTS / "reviewer_system.md").read_text(), "/ws/pr",
                      ART / "review_trajectory.json")
        result = agent.start(task)
        log(f"reviewer: {result}")
        if not result["exit_status"] == "Submitted":
            raise Stop(f"reviewer agent stopped: {result['exit_status']}")
        review, err = schemas.load_and_validate(OUT / "review.json", schemas.REVIEW)
        if review is None:
            result = agent.resume(f"Your review.json is invalid: {err}\nFix /ws/out/review.json, then finish.",
                                  "reviewer")
            review, err = schemas.load_and_validate(OUT / "review.json", schemas.REVIEW)
            if review is None:
                raise Stop(f"review.json failed its schema after one re-ask: {err}")

        # 3. G6: evidence check (ADR-007), one re-ask of the same reviewer when it fails.
        for attempt in range(MAX_STAGE_REASKS + 1):
            ok, msg = gates.g6(review, pr_ws, agent.commands_run(), new_failures)
            if ok or attempt == MAX_STAGE_REASKS:
                break
            log(f"G6 failed, one re-ask: {msg}")
            result = agent.resume(
                f"The evidence check failed:\n{msg}\n\nFix /ws/out/review.json. File evidence must be a path "
                "relative to /ws/pr and a line that exists. Command evidence must be a command you actually ran "
                "in this session; run it now if you have not. An approval needs a pytest command you ran and no "
                "major or blocker finding. Then finish.", "reviewer")
            review, err = schemas.load_and_validate(OUT / "review.json", schemas.REVIEW)
            if review is None:
                raise Stop(f"review.json failed its schema after the G6 re-ask: {err}")
        (ART / "review.json").write_text(json.dumps(review, indent=2))
        (ART / "g6.json").write_text(json.dumps({"passed": ok, "message": msg, "tokens": result["tokens"],
                                                 "model": result["model"]}, indent=2))
        if not ok:
            raise Stop(f"G6 evidence check failed: {msg}")

        # 4. Publish: PR comment + label (GITHUB_TOKEN, pull-requests: write).
        body = render(review)
        (ART / "review.md").write_text(body)
        gh_api(f"/repos/{REPO}/issues/{PR}/comments", method="POST", fields={"body": body})
        other = LABELS["approve" if review["verdict"] == "changes_requested" else "changes_requested"]
        try:
            gh_api(f"/repos/{REPO}/issues/{PR}/labels/{other}", method="DELETE")
        except RuntimeError:
            pass
        gh_api(f"/repos/{REPO}/issues/{PR}/labels", method="POST", fields={"labels": [LABELS[review["verdict"]]]})
        set_output("verdict", review["verdict"])
        set_output("status", "ready")
    except (Stop, RuntimeError, FileNotFoundError, json.JSONDecodeError) as e:
        write_explanation("Stage 6", "review stopped", str(e))
        set_output("status", "stop")
    except Exception as e:                          # never crash without an explanation (ADR-006)
        write_explanation("Stage 6", f"internal error: {type(e).__name__}", traceback.format_exc())
        set_output("status", "stop")
    finally:
        sandbox.stop()
        log(f"review of PR #{PR} for issue #{ISSUE} done")


if __name__ == "__main__":
    sys.exit(main())
