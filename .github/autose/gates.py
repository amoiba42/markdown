"""Deterministic checks G1-G4 and G6 (ADR-007). Each returns (passed: bool, message: str).

Anything that runs repository code or git on an agent-written workspace runs in the sandbox:
an agent can plant git config (for example core.fsmonitor) or test code that would otherwise run
on the runner next to the secrets.
"""

import ast
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import sandbox
from config import (MAX_DESIGN_TARGETS, PROTECTED_NAMES, PROTECTED_PREFIXES, PROTECTED_SUFFIXES,
                    TEST_DIR, WS)

IMPORT_NAME = os.environ.get("AUTOSE_IMPORT_NAME", "markdown")
AC_TEST = re.compile(r"^test_ac(\d+)_")


# ---------- helpers ----------

def safe_read(path: Path) -> str:
    """Read a file an agent wrote, refusing symlinks and paths outside the workspace root."""
    if path.is_symlink() or not str(path.resolve()).startswith(str(WS.resolve())):
        raise PermissionError(f"refusing to read {path}")
    return path.read_text(errors="replace")


def git(ws: Path, args: str) -> tuple[int, str]:
    return sandbox.run(f"git -c core.fsmonitor=false -c core.hooksPath=/dev/null {args}", cwd=sandbox.ws_path(ws))


def changed_files(ws: Path, base: str) -> list[str]:
    """Files changed in the working tree compared with `base`, including untracked files."""
    rc1, tracked = git(ws, f"diff --name-only {base}")
    rc2, untracked = git(ws, "ls-files --others --exclude-standard")
    if rc1 or rc2:
        raise RuntimeError(f"git failed in {ws.name}: {(tracked + untracked)[-500:]}")
    return sorted({f.strip() for f in (tracked + "\n" + untracked).splitlines() if f.strip()})


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith(TEST_DIR) and name.startswith("test_") and name.endswith(".py")


def is_protected(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (path.startswith(PROTECTED_PREFIXES) or name in PROTECTED_NAMES
            or (path.count("/") == 0 and name.endswith(PROTECTED_SUFFIXES)))


# ---------- G1: spec structure (the LLM judge is called by the orchestrator) ----------

def g1_structure(spec: dict) -> tuple[bool, str]:
    ids = [ac["id"] for ac in spec["acceptance_criteria"]]
    if len(ids) != len(set(ids)):
        return False, f"duplicate acceptance criterion ids: {ids}"
    return True, f"{len(ids)} acceptance criteria"


def g1_judge(spec: dict, verdicts: list) -> tuple[bool, str]:
    ids = {ac["id"] for ac in spec["acceptance_criteria"]}
    judged = {v["id"]: v for v in verdicts}
    missing = ids - judged.keys()
    untestable = [f"{i}: {judged[i]['reason']}" for i in ids & judged.keys() if not judged[i]["testable"]]
    if missing or untestable:
        return False, f"not judged: {sorted(missing)}; untestable: {untestable}"
    return True, "all acceptance criteria judged testable"


# ---------- G2: design targets ----------

def _symbols(source: str) -> set[str]:
    """Top-level functions/classes and Class.method names defined in a module."""
    names = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(f"{node.name}.{sub.name}")
    return names


def g2(design: dict, repo: Path) -> tuple[bool, str]:
    targets = design["targets"]
    if len(targets) > MAX_DESIGN_TARGETS:
        return False, f"{len(targets)} targets; at most {MAX_DESIGN_TARGETS} allowed"
    problems = []
    for t in targets:
        f = repo / t["file"]
        if is_protected(t["file"]) or ".." in t["file"]:
            problems.append(f"{t['file']}: protected or invalid path")
        elif t["status"] == "EXISTING":
            if not f.is_file():
                problems.append(f"{t['file']}: EXISTING file not found")
            elif t["kind"] != "module" and t["symbol"] not in _symbols(safe_read(f)):
                problems.append(f"{t['file']}: EXISTING symbol {t['symbol']} not found")
        elif not f.parent.is_dir():
            problems.append(f"{t['file']}: parent module {f.parent.relative_to(repo)} does not exist")
    return (not problems), "; ".join(problems) or f"{len(targets)} targets verified"


def interface_list(design: dict) -> list[dict]:
    """What the tester may see from the design: no `approach` (ADR-012)."""
    keep = ("file", "symbol", "kind", "signature", "status")
    return [{k: t[k] for k in keep} for t in design["targets"]]


# ---------- G3: build, lint, scope ----------

def scope_violations(changed: list[str], design: dict, managed_tests: set[str]) -> list[str]:
    allowed = {t["file"] for t in design["targets"]}
    bad = []
    for f in changed:
        if f in managed_tests:
            continue
        if is_protected(f):
            bad.append(f"{f} (protected)")
        elif f not in allowed and not is_test_file(f):
            bad.append(f"{f} (not a design target or test)")
    return bad


def g3(ws: Path, base: str, design: dict, managed_tests: set[str]) -> tuple[bool, str]:
    changed = changed_files(ws, base)
    bad = scope_violations(changed, design, managed_tests)
    if bad:
        return False, "scope guard: " + ", ".join(bad)
    rc, out = sandbox.run(f"rm -rf /tmp/whl && pip wheel -q --no-deps -w /tmp/whl . && python -c 'import {IMPORT_NAME}'",
                          cwd=sandbox.ws_path(ws), timeout=600)
    if rc != 0:
        return False, "build/import failed:\n" + out[-3000:]
    py = [f for f in changed if f.endswith(".py") and (ws / f).exists()]
    new = new_lint(ws, base, py)
    if new:
        return False, "ruff found new problems:\n" + "\n".join(new[:30])
    return True, f"build ok, ruff ok, {len(changed)} files in scope"


def _ruff_counts(ws: Path, path: str, source_cmd: str | None = None) -> dict[str, int]:
    """Count ruff problems per rule. With `source_cmd`, lint its stdout as if it were `path` (same config)."""
    q = sandbox.quote(path)
    cmd = (f"{source_cmd} | ruff check --output-format=json --exit-zero --stdin-filename {q} -" if source_cmd
           else f"ruff check --output-format=json --exit-zero {q}")
    _, out = sandbox.run(cmd, cwd=sandbox.ws_path(ws))
    counts: dict[str, int] = {}
    try:
        for item in json.loads(out[out.find("["):] or "[]"):
            counts[item["code"]] = counts.get(item["code"], 0) + 1
    except json.JSONDecodeError:
        counts["ruff-output-unreadable"] = 1
    return counts


def new_lint(ws: Path, base: str, files: list[str]) -> list[str]:
    """`ruff` passes means: no lint problem that was not already in the file at the base commit."""
    problems = []
    for f in files:
        after = _ruff_counts(ws, f)
        existed, _ = sandbox.run(f"git cat-file -e {base}:{sandbox.quote(f)}", cwd=sandbox.ws_path(ws))
        before = _ruff_counts(ws, f, f"git show {base}:{sandbox.quote(f)}") if existed == 0 else {}
        problems += [f"{f}: {code} x{n - before.get(code, 0)}" for code, n in after.items() if n > before.get(code, 0)]
    return problems


# ---------- G4: tests ----------

def run_pytest(ws: Path, junit_name: str, select: str = "") -> tuple[int, set[str], set[str], str]:
    """Run pytest in the sandbox. Returns (rc, passed ids, failed ids, output)."""
    junit = f"/tmp/{junit_name}.xml"
    # Editable install registers the package's entry points (Python-Markdown loads extensions through them).
    rc, out = sandbox.run(f"pip install -q --no-deps -e . >/dev/null 2>&1; python -m pytest -q -p no:cacheprovider "
                          f"--continue-on-collection-errors --junitxml={junit} {select}",
                          cwd=sandbox.ws_path(ws), timeout=900)
    _, xml = sandbox.run(f"cat {junit}")
    passed, failed = parse_junit(xml)
    return rc, passed, failed, out


def parse_junit(xml: str) -> tuple[set[str], set[str]]:
    passed, failed = set(), set()
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return passed, failed
    for tc in root.iter("testcase"):
        tid = f"{tc.get('classname')}::{tc.get('name')}"
        if tc.find("failure") is not None or tc.find("error") is not None:
            failed.add(tid)
        elif tc.find("skipped") is None:
            passed.add(tid)
    return passed, failed


def _assert_lines(source: str) -> dict[str, list[str]]:
    """For each test function, the source text of its assert statements and assert* calls."""
    tree, out = ast.parse(source), {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name.startswith("test"):
            items = []
            for n in ast.walk(fn):
                if isinstance(n, ast.Assert) or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                                 and n.func.attr.startswith("assert")):
                    items.append(ast.unparse(n))
            out[fn.name] = items
    return out


def asserts_unchanged(phase1: dict[str, str], phase2: dict[str, str]) -> list[str]:
    """Compare assert statements per test between tester phase 1 and phase 2 (ADR-012)."""
    changed = []
    for path, src1 in phase1.items():
        a1, a2 = _assert_lines(src1), _assert_lines(phase2.get(path, ""))
        for name, asserts in a1.items():
            if a2.get(name) != asserts:
                changed.append(f"{path}::{name}")
    return changed


def _meaningful(fn: ast.FunctionDef, symbols: set[str]) -> tuple[bool, bool]:
    """(references a design target, has a non-trivial assert)."""
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    calls_target = bool(names & symbols)
    real_assert = False
    for n in ast.walk(fn):
        if isinstance(n, ast.Assert) and not isinstance(n.test, ast.Constant):
            real_assert = True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr.startswith("assert") \
                and n.args and not all(isinstance(a, ast.Constant) for a in n.args):
            real_assert = True
    return calls_target, real_assert


def ac_coverage(spec: dict, tester_files: dict[str, str], passed: set[str], design: dict) -> list[str]:
    """Every AC needs a passing test_ac<n>_* that calls a design target and has a real assert."""
    symbols = set()
    for t in design["targets"]:
        symbols |= set(t["symbol"].split("."))
    symbols.add(IMPORT_NAME)
    good: dict[int, bool] = {}
    for src in tester_files.values():
        for fn in ast.walk(ast.parse(src)):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and (m := AC_TEST.match(fn.name)):
                ok_target, ok_assert = _meaningful(fn, symbols)
                did_pass = any(p.endswith("::" + fn.name) or ("::" + fn.name + "[") in p for p in passed)
                good[int(m.group(1))] = good.get(int(m.group(1)), False) or (ok_target and ok_assert and did_pass)
    problems = []
    for ac in spec["acceptance_criteria"]:
        n = int(ac["id"].split("-")[1])
        if not good.get(n):
            problems.append(f"{ac['id']} has no passing test_ac{n}_* that calls a design target with a real assert")
    return problems


def file_hashes(root: Path, paths: list[str]) -> dict[str, str]:
    return {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths if (root / p).is_file()}


def g4(ws: Path, spec: dict, design: dict, baseline_passed: set[str], tester_files: dict[str, str],
       existing_test_hashes: dict[str, str], changed: list[str]) -> tuple[bool, str, dict]:
    report = {}
    conftests = [f for f in changed if f.endswith("conftest.py")]
    if conftests:
        return False, f"conftest.py added or changed: {conftests}", report
    now = file_hashes(ws, list(existing_test_hashes))
    edited = [p for p, h in existing_test_hashes.items() if now.get(p) != h]
    if edited:
        return False, f"existing tests changed: {edited}", report
    _, passed, failed, out = run_pytest(ws, "g4")
    if failed:                                   # re-run once (flaky tests, ADR-007): a test passes if it passed in either run
        _, p2, _, out2 = run_pytest(ws, "g4_rerun")
        passed |= p2
        failed -= p2
        out += "\n--- re-run ---\n" + out2[-2000:]
    report = {"passed": len(passed), "failed": sorted(failed), "output_tail": out[-3000:]}
    lost = sorted(t for t in baseline_passed if t not in passed)
    if lost:
        return False, f"tests passing in baseline now fail or are missing: {lost[:20]}", report
    gaps = ac_coverage(spec, tester_files, passed, design)
    if gaps:
        return False, "; ".join(gaps), report
    return True, f"{len(passed)} tests pass, all ACs covered", report


# ---------- G6: review evidence ----------

def g6(review: dict, repo: Path, commands_run: list[str], new_failures: list[str]) -> tuple[bool, str]:
    problems = []

    def check(ev: dict, where: str):
        if "file" in ev:
            f = repo / ev["file"]
            try:
                n_lines = len(safe_read(f).splitlines()) if f.is_file() else 0
            except PermissionError:
                n_lines = 0
            if not (1 <= ev["line"] <= n_lines):
                problems.append(f"{where}: {ev['file']}:{ev['line']} does not exist")
        elif not any(ev["command"].strip() in c for c in commands_run):
            problems.append(f"{where}: command not run by the reviewer: {ev['command'][:80]}")

    for i, ev in enumerate(review["approval_evidence"]):
        check(ev, f"approval_evidence[{i}]")
    for f in review["findings"]:
        check(f["evidence"], f["id"])
    if review["verdict"] == "approve":
        test_cmds = [e for e in review["approval_evidence"] if "command" in e and "pytest" in e["command"]]
        if not test_cmds:
            problems.append("approval does not cite a test command the reviewer ran")
        if new_failures:
            problems.append(f"approval while the job's test step has new failures: {new_failures[:10]}")
        if review["findings"] and any(f["severity"] == "blocker" for f in review["findings"]):
            problems.append("approval with a blocker finding")
    return (not problems), "; ".join(problems) or "evidence valid"
