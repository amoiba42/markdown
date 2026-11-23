# Task: write the spec for issue #$number

$issue

The repository (Python-Markdown at the clean base commit) is at /ws/base. Read it as needed.

Write **/ws/out/spec.json** with exactly this shape:

```json
{
  "issue": $number,
  "summary": "one paragraph restating what the issue asks for",
  "acceptance_criteria": [
    {"id": "AC-1", "text": "Given ..., when ..., then ...", "verify_by": "what a pytest test would call and check"}
  ],
  "assumptions": ["<what was unclear>: assumed <reading>, because <reason>. Alternative not chosen: <other>. Empty list if none."],
  "out_of_scope": ["things the issue does not ask for"]
}
```

Rules: ids are AC-1, AC-2, ... in order; 1 to 6 criteria; each criterion is one observable behaviour.
Before writing criteria, search the existing tests (`grep -rn` under /ws/base/tests) for the behaviour the issue
touches. Existing tests are the project's contract: a criterion must not contradict one unless the issue
explicitly asks for that exact change. If the issue's examples are narrower than a general rule (for example
only headings with emphasis or code), keep the criteria that narrow and list the general case in out_of_scope.
When an existing test pins behaviour close to the change, add one criterion stating that this behaviour stays as
it is, with the test's own input and expected output, and do not write an assumption that widens the scope.

Only if the skill's rejection rule (Section 7) applies, write **/ws/out/rejected.json** instead of spec.json:
`{"reason": "...", "questions": ["..."]}` (1 to 5 questions).

Then write **/ws/out/attempt_summary.md**: at most 200 words on what you did and why.

$note
