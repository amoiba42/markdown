# Implementer notes for issue #3

# Design

Modify WikiLinksInlineProcessor.handleMatch to always return the literal matched string [[ ]] instead of an empty string, except when the whitespace-only wikilink is clearly embedded in plain text, where it should be replaced by two spaces to maintain backward compatibility for plain text behavior. We can identify plain text by checking if the surrounding characters in the data buffer are essentially word-like characters (or whitespace).

```json
[
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
The issue is that whitespace-only wikilinks (e.g., [[ ]]) vanish entirely, which is undesired in contexts like tables, headers, and emphasis.

The fix involves modifying `WikiLinksInlineProcessor.handleMatch` in `markdown/extensions/wikilinks.py`. Currently, the processor explicitly returns an empty string when `m.group(1).strip()` is falsy (which happens for `[[ ]]`). 

Instead, the logic will now return the original matched text `m.group(0)` (the string `[[ ]]`) by default. To preserve the existing behavior (AC-4) where `foo [[ ]] bar` becomes `foo  bar` in plain text, we will conditionally return `  ` only when the context indicates it is plain text. Specifically, we will check if the character immediately preceding the match and the character immediately following the match are not special Markdown formatting symbols (like `*`, `_`, `[`, etc.).

This approach satisfies all acceptance criteria:
- AC-1, AC-2, AC-3: The `[[ ]]` string will be preserved in emphasis, tables, and headers.
- AC-4: The `[[ ]]` will still be replaced by two spaces in plain text contexts.
- Cleanup: I will remove the stray comments currently present in the file.

## Implementation attempt summary
Modified `WikiLinksInlineProcessor.handleMatch` in `/ws/work/markdown/extensions/wikilinks.py` to handle whitespace-only wikilinks (`[[ ]]`) contextually.

The fix checks the `data` parameter (current inline context) for word characters (`\w`) on both sides of the match:
- If word chars exist on both sides → return empty string (preserves AC-4: `foo [[ ]] bar` → `foo  bar`)
- Otherwise → return literal `[[ ]]` (keeps it visible in emphasis AC-1, table cells AC-2, headings AC-3)

Using `\w` instead of `\S` avoids collapsing inside emphasis (where `*` delimiters are non-word) and other Markdown syntax contexts. All four acceptance criteria pass and all existing tests (902) continue to pass.
