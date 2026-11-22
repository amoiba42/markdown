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

Rules for targets (checked by a script):
- List every file and function, class or method you will add or change. At most 10 targets.
- `status` is EXISTING if the file and symbol exist now (methods as Class.method), otherwise NEW.
- A NEW file must go in an existing package directory. `kind` is function, class, method or module.
- Include the signature: tests are written from it before your code exists.
- Tests are not targets; do not list test files.

Do not change code yet. When design.json is written, finish.

$note
