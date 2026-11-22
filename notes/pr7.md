# Implementer notes for issue #5

# Design

Modify the wikilinks extension to support custom labels via [[Target|Label]] syntax. Change the regex pattern in WikiLinkExtension.extendMarkdown from r'\[\[([\w0-9_ -]+)\]\]' to r'\[\[([\w0-9_ -]+)(?:\|([\w0-9_ -]+))?\]\]' to optionally capture a label after a pipe. In WikiLinksInlineProcessor.handleMatch, extract target (group 1) and label (group 2, falling back to target if absent), strip whitespace from both, build the URL from the target, and set the link text to the label. Plain [[Name]] wikilinks continue to work unchanged because group 2 will be None and label defaults to target.

```json
[
  {
    "file": "markdown/extensions/wikilinks.py",
    "symbol": "WikiLinkExtension.extendMarkdown",
    "kind": "method",
    "signature": "def extendMarkdown(self, md) -> None",
    "status": "EXISTING"
  },
  {
    "file": "markdown/extensions/wikilinks.py",
    "symbol": "WikiLinksInlineProcessor.handleMatch",
    "kind": "method",
    "signature": "def handleMatch(self, m: re.Match[str], data: str) -> tuple[etree.Element | str, int, int]",
    "status": "EXISTING"
  }
]
```

## Design attempt summary
## Approach Summary

The change extends the wikilinks inline processor to support `[[Target|Label]]` syntax while preserving existing `[[Name]]` behavior.

**Changes:**
1. **Regex update** (`WikiLinkExtension.extendMarkdown`): The pattern changes from `r'\[\[([\w0-9_ -]+)\]\]'` to `r'\[\[([\w0-9_ -]+)(?:\|([\w0-9_ -]+))?\]\]'`. This adds an optional non-capturing group for a pipe followed by a label. The character class excludes `|` and `]`, so only a single pipe is supported as a separator.

2. **Match handling** (`WikiLinksInlineProcessor.handleMatch`): Extract `target = m.group(1).strip()` and `label = m.group(2).strip() if m.group(2) else target`. If `target` is empty after stripping, return no link (preserving current `[[ ]]` behavior). Build the URL from `target` using the existing `build_url` callable, and set the link text to `label`.

**Why this works:** Plain wikilinks `[[Name]]` have no pipe, so `m.group(2)` is `None` and `label` falls back to `target`, producing identical output to today. Custom-label wikilinks `[[Target|Label]]` populate both groups; whitespace is stripped from both per the spec assumption. The existing config options (`base_url`, `end_url`, `html_class`, `build_url`, meta-data overrides) remain unchanged because URL construction still uses only the target portion.

**Test compatibility:** All existing tests use plain `[[...]]` syntax and will continue to pass. No test changes are needed.

## Implementation attempt summary
## Implementation Summary

Modified `/ws/work/markdown/extensions/wikilinks.py` to support custom link labels via `[[Target|Label]]` syntax:

**Changes made:**

1. **Regex pattern** (`WikiLinkExtension.extendMarkdown`, line 58): Changed from `r'\[\[([\w0-9_ -]+)\]\]'` to `r'\[\[([\w0-9_ -]+)(?:\|([\w0-9_ -]+))?\]\]'`. The new pattern adds an optional non-capturing group `(?:\|([\w0-9_ -]+))?` that matches a pipe followed by a label. Both target and label use the same character class (word chars, spaces, hyphens, underscores).

2. **Match handling** (`WikiLinksInlineProcessor.handleMatch`, lines 73-84): 
   - Extract `target = m.group(1).strip()` 
   - Extract `label = m.group(2).strip() if m.group(2) else target` (falls back to target when no pipe present)
   - Build URL from `target` using existing `build_url` callable
   - Set link text to `label`, wrapped in `AtomicString` for custom labels to prevent further markdown processing (plain wikilinks continue to allow markdown processing in link text, preserving existing behavior)
   - Preserve existing behavior for empty target (returns no link)

**Behavior:**
- `[[Release Notes|what is new]]` → `<a href="/Release_Notes/">what is new</a>`
- `[[Release Notes]]` → `<a href="/Release_Notes/">Release Notes</a>` (unchanged, including markdown processing in link text)
- `[[Target|label with _underscores_]]` → label text is literal, no emphasis processing
- `[[ Target | Label ]]` → whitespace stripped from both parts

All 907 tests pass (902 original + 5 new custom label tests). The pre-existing collection error in `test_md_in_html.py` is unrelated.
