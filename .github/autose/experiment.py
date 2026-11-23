"""Decision experiments (experiment/variants.md). Run by .github/workflows/autose-experiment.yml.

Both modes post nothing (AUTOSE_NO_COMMENTS=1, AUTOSE_REVIEW_PUBLISH=0), never touch an issue's state bundle and
write their results to the artifact folder only. Models are pinned per role by the workflow (AUTOSE_PIN_MODEL_*).

  EXP_MODE=d1  Stage 1 only for one issue; EXP_VARIANT=skill | bare (AUTOSE_A1_SKILL on/off).
  EXP_MODE=d8  independent review of one pull request; EXP_VARIANT=blind | notes. With notes, the reviewer also
               gets the implementer's design and attempt summaries (AUTOSE_REVIEWER_NOTES).
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["AUTOSE_NO_COMMENTS"] = "1"
os.environ["AUTOSE_REVIEW_PUBLISH"] = "0"

import gates      # noqa: E402
import sandbox    # noqa: E402
from common import ART, ISSUE, REPO, RUN_URL, gh_api, log  # noqa: E402
from config import WS  # noqa: E402

MODE = os.environ["EXP_MODE"]
VARIANT = os.environ["EXP_VARIANT"]
CHECKOUT = Path(os.environ.get("AUTOSE_CHECKOUT", "work"))     # the base branch
RESULT = ART / "experiment.json"


def write_result(data: dict) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    base = {"mode": MODE, "variant": VARIANT, "issue": ISSUE, "run": RUN_URL,
            "rep": os.environ.get("EXP_REP", "1"), "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    RESULT.write_text(json.dumps(base | data, indent=2))
    log(f"experiment result: {base | data}")


def d1() -> None:
    import orchestrator
    from agents import RUN_TOKENS
    os.environ["AUTOSE_A1_SKILL"] = "off" if VARIANT == "bare" else "on"
    state = orchestrator.new_state()
    WS.mkdir(parents=True, exist_ok=True)
    orchestrator.copy_ws(CHECKOUT, WS / "base")
    sandbox.start()
    sandbox.lockdown()
    try:
        orchestrator.snapshot(state)
        orchestrator.stage1(state, "")
        outcome, reason = "passed", ""
    except orchestrator.Stop as s:
        outcome, reason = "stopped", f"{s.reason}: {s.detail[:1500]}"
    except Exception as e:     # recorded, not raised: the experiment compares outcomes
        outcome, reason = "error", f"{type(e).__name__}: {str(e)[:1500]}"
    finally:
        sandbox.stop()
    spec = json.loads((ART / "spec.json").read_text()) if (ART / "spec.json").exists() else None
    judge = json.loads((ART / "judge.json").read_text()) if (ART / "judge.json").exists() else None
    write_result({
        "outcome": outcome, "reason": reason,
        "rejected": (ART / "rejected.json").exists(),
        "n_criteria": len(spec["acceptance_criteria"]) if spec else 0,
        "n_assumptions": len(spec["assumptions"]) if spec else 0,
        "judge_testable": sum(1 for v in judge or [] if v.get("testable")),
        "judge_total": len(judge or []),
        "models": state.get("models_used"), "tokens": state.get("tokens"), "run_tokens": RUN_TOKENS["total"],
    })


def d8() -> None:
    inp = Path(os.environ["AUTOSE_REVIEW_INPUT"])
    inp.mkdir(parents=True, exist_ok=True)
    issue = gh_api(f"/repos/{REPO}/issues/{ISSUE}")
    (inp / "issue.json").write_text(json.dumps({"title": issue["title"], "body": issue.get("body") or ""}))
    # Baseline on the base branch, in the sandbox, as the main workflow does (ADR-007).
    WS.mkdir(parents=True, exist_ok=True)
    if (WS / "base").exists():
        shutil.rmtree(WS / "base")
    shutil.copytree(CHECKOUT, WS / "base", symlinks=True)
    sandbox.start()
    sandbox.lockdown()
    _, passed, failed, _ = gates.run_pytest(WS / "base", "baseline")
    (inp / "baseline.json").write_text(json.dumps({"passed": sorted(passed), "failed": sorted(failed)}))
    notes = Path(os.environ.get("EXP_NOTES_DIR", "")) / f"{os.environ['EXP_SUBJECT']}.md"
    if VARIANT == "notes":
        if not notes.is_file():
            write_result({"outcome": "error", "reason": f"no implementer notes at {notes}"})
            return
        os.environ["AUTOSE_REVIEWER_NOTES"] = str(notes)
    os.environ["AUTOSE_SANDBOX_READY"] = "1"
    import review
    review.main()                          # writes review.json, g6.json or explanation.md into ART
    rv = json.loads((ART / "review.json").read_text()) if (ART / "review.json").exists() else None
    g6 = json.loads((ART / "g6.json").read_text()) if (ART / "g6.json").exists() else {}
    write_result({
        "subject": os.environ["EXP_SUBJECT"],
        "outcome": "reviewed" if rv else "stopped",
        "reason": "" if rv else (ART / "explanation.md").read_text()[:1500] if (ART / "explanation.md").exists() else "",
        "verdict": rv["verdict"] if rv else None,
        "findings": [{k: f[k] for k in ("id", "classification", "severity", "explanation")} for f in rv["findings"]]
        if rv else [],
        "g6": g6.get("message"), "model": g6.get("model"), "tokens": g6.get("tokens"),
    })


if __name__ == "__main__":
    {"d1": d1, "d8": d8}[MODE]()
    sys.exit(0)
