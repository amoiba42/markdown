"""Job 4: Route (ADR-003, ADR-006). A script, no agent; actions: write and issues: write; if: always().

Reads the job results, the review verdict and the review round, then:
- approved, or two review rounds used  -> mark done, hand over to a human;
- changes requested and a round is left -> record the target stage in state.json, start a new run;
- any check failed, cap reached, or a job crashed / timed out / was cancelled -> post the stop comment.
"""

import json
import os
import shutil
import sys

from common import (ART, ISSUE, REPO, RUN_URL, comment, download_latest_bundle, gh_api, load_state, log,
                    read, save_state, update_mirror)
from config import BASE_REF, MAX_REVIEW_ROUNDS

STAGE_OF = {"spec": 1, "design": 2, "code": 3, "test": 4}
RESULTS = {job: os.environ.get(f"RESULT_{job.upper()}", "") for job in ("orchestrator", "release", "review")}
STATUS = {job: os.environ.get(f"STATUS_{job.upper()}", "") for job in ("orchestrator", "release", "review")}


def stop_reason() -> str | None:
    for job in ("orchestrator", "release", "review"):
        if RESULTS[job] in ("failure", "cancelled"):
            return f"the {job} job {RESULTS[job]} (crash, timeout or cancellation)"
        if STATUS[job] == "stop":
            sub = {"orchestrator": ART, "release": ART / "release", "review": ART / "review"}[job]
            return read(sub / "explanation.md") or f"the {job} job stopped"
    return None


def main() -> None:
    if not (ART / "state.json").exists():
        download_latest_bundle()
    state = load_state()
    if STATUS["orchestrator"] == "done":
        log("issue already done; nothing to route")
        return
    rel = ART / "release" / "release_state.json"
    if rel.exists():
        r = json.loads(rel.read_text())
        state["released_sha"], state["pr"] = r.get("branch_head"), r.get("pr")

    reason = stop_reason()
    if reason:
        state["status"] = "stopped"
        comment(f"**AutoSE stopped.**\n\n{reason[:3000]}\n\nExplanation artifact: {RUN_URL}")
    else:
        review = json.loads(read(ART / "review" / "review.json") or "{}")
        verdict = review.get("verdict")
        state["review_round"] += 1
        if (ART / "review" / "review.json").exists():
            shutil.copy(ART / "review" / "review.json", ART / "review.json")
        if verdict == "approve" or state["review_round"] >= MAX_REVIEW_ROUNDS:
            state.update(done=True, status="done", target_stage=None)
            why = "approved" if verdict == "approve" else f"{MAX_REVIEW_ROUNDS} review rounds used"
            comment(f"**AutoSE finished ({why}).** PR #{state.get('pr')} is ready for a human decision. "
                    "AutoSE never merges.")
        else:
            target = min(STAGE_OF[f["classification"]] for f in review["findings"]) if review["findings"] else 3
            state.update(target_stage=target, last_passed=target - 1, sendback="review", status="re-entry")
            save_state(state)
            gh_api(f"/repos/{REPO}/actions/workflows/autose.yml/dispatches", method="POST",
                   fields={"ref": BASE_REF, "inputs": {"issue": str(ISSUE)}})
            log(f"re-entry dispatched at stage {target}")
    save_state(state)
    try:
        update_mirror(state, f"route: {state['status']}")
    except Exception as e:
        log(f"mirror update failed: {e}")


if __name__ == "__main__":
    sys.exit(main())
