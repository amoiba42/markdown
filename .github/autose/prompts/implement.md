# Task: implement the approved design

$context

## Design (design.json)
```json
$design
```

Implement the design in /ws/work. Change only the target files. You may add tests of your own, but only as
files named tests/**/test_*.py; any other new or changed file is rejected by the scope check.
Run the existing tests with `cd /ws/work && python -m pytest -q` and make sure you did not break them. Every test
that passed before your change must still pass: existing tests define behaviour you must not change. If one
fails, narrow your change instead of editing the test.
Do not loop on checking one criterion: an independent tester checks the acceptance criteria later. Check each
criterion at most twice; when the change is made and the existing tests pass, stop. Write scratch files only under
/tmp, never in /ws/work.
Then write **/ws/out/attempt_summary.md**: at most 200 words on what you changed and why.

$note
