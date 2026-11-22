import markdown
import pytest

def test_ac1_whitespace_wikilink_in_emphasis():
    """
    AC-1: Given markdown text with emphasis containing a whitespace-only wikilink like *see [[ ]]* 
    and the wikilinks extension enabled, when converted to HTML, then the resulting output 
    contains the literal text [[ ]] inside the emphasis, such as <p><em>see [[ ]]</em></p>.
    """
    result = markdown.markdown("*see [[ ]]*", extensions=["markdown.extensions.wikilinks"])
    assert result == "<p><em>see [[ ]]</em></p>"

def test_ac2_whitespace_wikilink_in_table():
    """
    AC-2: Given markdown text with a table cell containing a whitespace-only wikilink like | [[ ]] | 
    and the wikilinks and tables extensions enabled, when converted to HTML, then the 
    resulting table cell contains the literal text [[ ]], rather than being empty.
    """
    result = markdown.markdown("| [[ ]] |\n|---|", extensions=["markdown.extensions.wikilinks", "markdown.extensions.tables"])
    # The exact HTML for a table can be complex, but it should at least contain the [[ ]]
    assert "[[ ]]" in result

def test_ac3_whitespace_wikilink_in_heading():
    """
    AC-3: Given markdown text with a heading containing a whitespace-only wikilink like # [[ ]] 
    and the wikilinks and toc extensions enabled, when converted to HTML, then the heading 
    contains the literal text [[ ]], and the table of contents entry is not empty.
    """
    # Note: TOC extension inserts a placeholder like [TOC]
    result = markdown.markdown("# [[ ]]\n[TOC]", extensions=["markdown.extensions.wikilinks", "markdown.extensions.toc"])
    assert "[[ ]]" in result
    assert "toc" in result

def test_ac4_whitespace_wikilink_in_plain_text():
    """
    AC-4: Given markdown text with plain text containing a whitespace-only wikilink like 
    foo [[ ]] bar and the wikilinks extension enabled, when converted to HTML, then 
    existing behavior where the whitespace-only wikilink is replaced by two spaces 
    (foo  bar) remains unchanged.
    """
    # The issue states: "The same thing happens in a table cell ... The same thing happens in a heading ...
    # I'd expect [[ ]] to be kept as typed, just like [[]]."
    # Wait, the AC-4 text says: "when converted to HTML, then existing behavior where the 
    # whitespace-only wikilink is replaced by two spaces (foo  bar) remains unchanged."
    # Let me re-read AC-4.
    result = markdown.markdown("foo [[ ]] bar", extensions=["markdown.extensions.wikilinks"])
    assert result == "<p>foo  bar</p>"
