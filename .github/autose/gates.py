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
    # .claude/ is Claude Code's own state if it ever writes any into a workspace; never part of a change.
    return sorted({f.strip() for f in (tracked + "\n" + untracked).splitlines()
                   if f.strip() and not f.strip().startswith(".claude/")})


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

def definitions(source: str) -> dict[str, str]:
    """Every name a design target can point at, with its kind: classes, functions (nested as outer.inner),
    methods (Class.method, also in nested classes), and attributes (module constants as NAME, class
    attributes as Class.NAME). Definitions under module- or class-level if/try/with count too."""
    found: dict[str, str] = {}

    def visit(body: list, prefix: str, in_class: bool, in_function: bool) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[prefix + node.name] = "method" if in_class else "function"
                visit(node.body, f"{prefix}{node.name}.", False, True)
            elif isinstance(node, ast.ClassDef):
                found[prefix + node.name] = "class"
                visit(node.body, f"{prefix}{node.name}.", True, False)
            elif not in_function and isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            found.setdefault(prefix + n.id, "attribute")
            elif not in_function and isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                for part in ("body", "orelse", "finalbody"):
                    visit(getattr(node, part, []) or [], prefix, in_class, in_function)
                for h in getattr(node, "handlers", []) or []:
                    visit(h.body, prefix, in_class, in_function)

    visit(ast.parse(source).body, "", False, False)
    return found


def g2(design: dict, repo: Path) -> tuple[bool, str]:
    targets = design["targets"]
    if len(targets) > MAX_DESIGN_TARGETS:
        return False, f"{len(targets)} targets; at most {MAX_DESIGN_TARGETS} allowed"
    problems = []
    for t in targets:
        f, kind, sym = repo / t["file"], t["kind"], t["symbol"]
        where = f"{t['file']} {sym}"
        if is_protected(t["file"]) or ".." in t["file"] or t["file"].startswith("/"):
            problems.append(f"{t['file']}: protected or invalid path (paths are relative to the repository root)")
            continue
        if not t["file"].endswith(".py") and kind != "module":
            problems.append(f"{where}: {t['file']} is not a Python file; use kind \"module\" for the whole file")
            continue
        if t["status"] == "NEW":
            if not f.parent.is_dir():
                problems.append(f"{where}: parent directory {f.parent.relative_to(repo)} does not exist")
            elif kind == "module" and f.exists():
                problems.append(f"{where}: marked NEW but the file already exists; use EXISTING")
            elif kind != "module" and f.is_file():
                try:
                    if sym in definitions(safe_read(f)):
                        problems.append(f"{where}: marked NEW but already defined in the file; use EXISTING")
                except SyntaxError:
                    pass                      # an unparsable file is reported when it is a target as EXISTING
            continue
        if not f.is_file():
            problems.append(f"{where}: EXISTING file not found")
            continue
        if kind == "module":
            continue
        try:
            defs = definitions(safe_read(f))
        except SyntaxError as e:
            problems.append(f"{where}: cannot parse {t['file']}: {e.msg} (line {e.lineno})")
            continue
        if sym not in defs:
            near = sorted(d for d in defs if d.split(".")[-1] == sym.split(".")[-1])[:5]
            hint = f"; did you mean {near}?" if near else ""
            problems.append(f"{where}: EXISTING symbol not found{hint}")
        elif defs[sym] != kind:
            problems.append(f"{where}: is a {defs[sym]}, not a {kind}")
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
    rc, out = sandbox.run(f"rm -rf /tmp/whl && pip wheel -q --no-deps --no-build-isolation -w /tmp/whl . && python -c 'import {IMPORT_NAME}'",
                          cwd=sandbox.ws_path(ws), timeout=600)
    if rc != 0:
        return False, "build/import failed:\n" + out[-3000:]
    py = [f for f in changed if f.endswith(".py") and (ws / f).exists()]
    new = new_lint(ws, base, py)
    if new:
        return False, "ruff found new problems:\n" + "\n".join(new[:30])
    return True, f"build ok, ruff ok, {len(changed)} files in scope"


def _ruff_counts(ws: Path, path: str, source_cmd: str | None = None) -> tuple[dict[str, int], list[dict]]:
    """Count ruff problems per rule, and return the problems. With `source_cmd`, lint its stdout as if it
    were `path` (same config)."""
    q = sandbox.quote(path)
    cmd = (f"{source_cmd} | ruff check --output-format=json --exit-zero --stdin-filename {q} -" if source_cmd
           else f"ruff check --output-format=json --exit-zero {q}")
    _, out = sandbox.run(cmd, cwd=sandbox.ws_path(ws))
    counts: dict[str, int] = {}
    items: list[dict] = []
    try:
        items = json.loads(out[out.find("["):] or "[]")
        for item in items:
            counts[item["code"]] = counts.get(item["code"], 0) + 1
    except json.JSONDecodeError:
        counts["ruff-output-unreadable"] = 1
    return counts, items


def new_lint(ws: Path, base: str, files: list[str]) -> list[str]:
    """`ruff` passes means: no lint problem that was not already in the file at the base commit."""
    problems = []
    for f in files:
        after, items = _ruff_counts(ws, f)
        existed, _ = sandbox.run(f"git cat-file -e {base}:{sandbox.quote(f)}", cwd=sandbox.ws_path(ws))
        before = _ruff_counts(ws, f, f"git show {base}:{sandbox.quote(f)}")[0] if existed == 0 else {}
        for code, n in after.items():
            if n > before.get(code, 0):
                problems.append(f"{f}: {code} x{n - before.get(code, 0)} new")
                for it in [i for i in items if i.get("code") == code][:5]:
                    loc = it.get("location") or {}
                    problems.append(f"  {f}:{loc.get('row')}:{loc.get('column')} {code} {it.get('message', '')}")
    return problems


# ---------- G4: tests ----------

def run_pytest(ws: Path, junit_name: str, select: str = "") -> tuple[int, set[str], set[str], str]:
    """Run pytest in the sandbox. Returns (rc, passed ids, failed ids, output)."""
    junit = f"/tmp/{junit_name}.xml"
    # Editable install registers the package's entry points (Python-Markdown loads extensions through them).
    rc, out = sandbox.run(f"pip install -q --no-deps --no-build-isolation -e . >/dev/null 2>&1; python -m pytest -q -p no:cacheprovider "
                          f"--continue-on-collection-errors --junitxml={junit} {select}",
                          cwd=sandbox.ws_path(ws), timeout=900)
    _, xml = sandbox.run(f"cat {junit}")
    passed, failed = parse_junit(xml)
    return rc, passed, failed, out


def junit_failures(junit_name: str, limit: int = 15) -> dict[str, str]:
    """First line of the error for each failed test in the last run_pytest(junit_name): test id -> message."""
    _, xml = sandbox.run(f"cat /tmp/{junit_name}.xml")
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for tc in root.iter("testcase"):
        bad = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
        if bad is not None and len(out) < limit:
            msg = (bad.get("message") or (bad.text or "")).strip().splitlines()
            out[f"{tc.get('classname')}::{tc.get('name')}"] = (msg[0] if msg else "")[:200]
    return out


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


def syntax_errors(files: dict[str, str]) -> list[str]:
    """Files that do not parse, with the error, so a broken test file fails a check instead of crashing it."""
    bad = []
    for path, src in files.items():
        try:
            ast.parse(src)
        except SyntaxError as e:
            bad.append(f"{path}: {e.msg} (line {e.lineno})")
    return bad


def _test_functions(tree: ast.AST, prefix: str = ""):
    """(qualified name, node) for every test function, with the class name as prefix (Class.test_x)."""
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.ClassDef):
            yield from _test_functions(node, f"{prefix}{node.name}.")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            yield prefix + node.name, node


def _assert_lines(source: str) -> dict[str, list[str]]:
    """For each test function, the source text of its assert statements, assert* calls and pytest.raises/warns
    contexts. Keyed by Class.test_name, so equal names in different classes stay apart."""
    out = {}
    for name, fn in _test_functions(ast.parse(source)):
        items = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Assert) or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                             and n.func.attr.startswith("assert")):
                items.append(ast.unparse(n))
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                items += [ast.unparse(i.context_expr) for i in n.items if _is_raises(i.context_expr)]
        out[name] = items
    return out


def asserts_unchanged(phase1: dict[str, str], phase2: dict[str, str]) -> list[str]:
    """Compare assert statements per test between tester phase 1 and phase 2 (ADR-012).
    Both sides must parse; the caller checks that first."""
    changed = []
    for path, src1 in phase1.items():
        a1, a2 = _assert_lines(src1), _assert_lines(phase2.get(path, ""))
        for name, asserts in a1.items():
            if a2.get(name) != asserts:
                changed.append(f"{path}::{name}")
    return changed


RAISES = {"raises", "warns", "deprecated_call", "assertRaises", "assertRaisesRegex", "assertWarns"}


def _is_raises(expr: ast.AST) -> bool:
    """`pytest.raises(...)`, `raises(...)`, `self.assertRaises(...)` and the warning variants."""
    if not isinstance(expr, ast.Call):
        return False
    f = expr.func
    return (isinstance(f, ast.Attribute) and f.attr in RAISES) or (isinstance(f, ast.Name) and f.id in RAISES)


def _imported_library_names(tree: ast.AST) -> set[str]:
    """Names a test file binds from the library: `from markdown import Markdown` gives Markdown."""
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == IMPORT_NAME:
            names |= {a.asname or a.name for a in n.names}
        elif isinstance(n, ast.Import):
            names |= {(a.asname or a.name).split(".")[0] for a in n.names if a.name.split(".")[0] == IMPORT_NAME}
    return names


def _meaningful(fn: ast.FunctionDef, symbols: set[str]) -> tuple[bool, bool]:
    """(references a design target or the library, has a non-trivial assert or an expected-exception check)."""
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
        if isinstance(n, (ast.With, ast.AsyncWith)) and any(_is_raises(i.context_expr) for i in n.items):
            real_assert = True
    return calls_target, real_assert


def ac_coverage(spec: dict, tester_files: dict[str, str], passed: set[str], design: dict) -> list[str]:
    """Every AC needs a passing test_ac<n>_* that calls a design target (or the library) and has a real assert.
    The tester files must parse; the caller checks that first."""
    symbols = set()
    for t in design["targets"]:
        symbols |= set(t["symbol"].split("."))
    symbols.add(IMPORT_NAME)
    good: dict[int, bool] = {}
    why: dict[int, str] = {}
    for src in tester_files.values():
        tree = ast.parse(src)
        file_symbols = symbols | _imported_library_names(tree)
        for _, fn in _test_functions(tree):
            if not (m := AC_TEST.match(fn.name)):
                continue
            n = int(m.group(1))
            ok_target, ok_assert = _meaningful(fn, file_symbols)
            did_pass = any(p.endswith("::" + fn.name) or ("::" + fn.name + "[") in p for p in passed)
            good[n] = good.get(n, False) or (ok_target and ok_assert and did_pass)
            if not good[n]:
                why[n] = ("fails" if not did_pass else "") or ("calls no design target" if not ok_target
                                                                else "has no real assert")
    problems = []
    for ac in spec["acceptance_criteria"]:
        n = int(ac["id"].split("-")[1])
        if not good.get(n):
            detail = f" (test_ac{n}_* {why[n]})" if n in why else f" (no test_ac{n}_* exists)"
            problems.append(f"{ac['id']} is not covered{detail}")
    return problems


def file_hashes(root: Path, paths: list[str]) -> dict[str, str]:
    return {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths if (root / p).is_file()}


def g4(ws: Path, spec: dict, design: dict, baseline_passed: set[str], baseline_failed: set[str],
       tester_files: dict[str, str],
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
    errors = {t: m for t, m in junit_failures("g4_rerun" if failed else "g4").items() if t in failed}
    report = {"passed": len(passed), "failed": sorted(failed), "errors": errors, "output_tail": out[-3000:]}
    # The implementer never sees the tester's tests (ADR-012): the note names failing tests and the first
    # line of each error, not the test source.
    failing = "\n".join(f"- {t}: {m}" for t, m in list(errors.items())[:15]) or "- (no error text captured)"
    lost = sorted(t for t in baseline_passed if t not in passed)
    new_failures = sorted(failed - baseline_failed)     # all new and existing tests must pass (brief, Stage 4)
    if new_failures and not lost:
        return False, (f"tests fail that did not fail at the base commit: {new_failures[:20]}\n"
                       f"Failing tests and their first error line:\n{failing}"), report
    if lost:
        return False, (f"tests passing in baseline now fail or are missing: {lost[:20]}\n"
                       f"Failing tests and their first error line:\n{failing}"), report
    gaps = ac_coverage(spec, tester_files, passed, design)
    if gaps:
        return False, "; ".join(gaps) + f"\nFailing tests and their first error line:\n{failing}", report
    return True, f"{len(passed)} tests pass, all ACs covered", report


# ---------- G6: review evidence ----------

def _norm_command(cmd: str) -> str:
    """Compare commands without a leading `cd /ws/pr &&` and with spaces collapsed."""
    cmd = re.sub(r"^\s*cd\s+/ws/pr\s*(&&|;)\s*", "", cmd.strip())
    return re.sub(r"\s+", " ", cmd)


def g6(review: dict, repo: Path, commands_run: list[str], new_failures: list[str]) -> tuple[bool, str]:
    problems = []
    ran = [_norm_command(c) for c in commands_run]

    def check(ev: dict, where: str):
        if "file" in ev:
            rel = ev["file"].removeprefix("/ws/pr/").removeprefix("./")   # the prompt asks for relative paths
            f = repo / rel
            try:
                n_lines = len(safe_read(f).splitlines()) if f.is_file() else 0
            except PermissionError:
                n_lines = 0
            if not (1 <= ev["line"] <= n_lines):
                problems.append(f"{where}: {ev['file']}:{ev['line']} does not exist")
        elif not any(_norm_command(ev["command"]) in c for c in ran):
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
        serious = [f["id"] for f in review["findings"] if f["severity"] in ("major", "blocker")]
        if serious:
            problems.append(f"approval with major or blocker findings {serious}; use changes_requested")
    return (not problems), "; ".join(problems) or "evidence valid"
