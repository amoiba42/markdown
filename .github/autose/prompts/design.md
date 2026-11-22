# Task: design the change

$issue

## Spec (spec.json)
```json
$spec
```

The repository is at /ws/work. Read ARCHITECTURE.md and the relevant modules, then write **/ws/out/design.json**:

```json
{
  "approach": "short prose plan",
  "targets": [
    {"file": "markdown/extensions/toc.py", "symbol": "TocTreeprocessor.run", "kind": "method",
     "signature": "def run(self, doc) -> None", "status": "EXISTING"}
  ]
}
```

Rules for targets (checked by a script that parses the files):
- List every file and function, class, method or attribute you will add or change. At most 10 targets.
- `file` is a path relative to the repository root.
- `symbol` is the dotted name inside the file: `func`, `Class`, `Class.method`, `Class.ATTRIBUTE`,
  `MODULE_CONSTANT`, `outer.inner` for a nested function. For a whole file use the module name and kind `module`.
- `kind` is function, class, method (a def inside a class), attribute (a class or module-level assignment,
  e.g. a compiled regex `Class.RE`), or module (the whole file; required for non-Python files).
- `status` is EXISTING if that symbol is defined in that file now, otherwise NEW. A NEW file must go in an
  existing directory and must not exist yet.
- Include the signature (for an attribute, its value's shape): tests are written from it before your code exists.
- Tests are not targets; do not list test files.

Do not change code yet. When design.json is written, write **/ws/out/attempt_summary.md** (at most 200 words:
the approach and why), then finish.

$note
