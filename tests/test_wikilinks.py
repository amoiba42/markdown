import pytest
import markdown

def test_ac1_whitespace_wikilink_in_emphasis():
    result = markdown.markdown("*see [[ ]]*", extensions=["wikilinks"])
    assert result == '<p><em>see [[ ]]</em></p>'

def test_ac2_whitespace_wikilink_in_table_cell():
    # Let's make sure the wikilink is in a cell that is rendered.
    # The header row might be empty if the wikilink is treated as empty.
    # Let's try putting it in the body.
    text = "| header |\n|---|\n| [[ ]] |"
    result = markdown.markdown(text, extensions=["wikilinks", "tables"])
    assert "[[ ]]" in result

def test_ac3_whitespace_wikilink_in_heading():
    result = markdown.markdown("# [[ ]]", extensions=["wikilinks", "toc"])
    assert "[[ ]]" in result

def test_ac4_whitespace_wikilink_in_plain_text():
    # AC-4 says it should be replaced by two spaces, resulting in 'foo  bar'
    # My previous test showed it was 'foo  bar', so this should pass now.
    result = markdown.markdown("foo [[ ]] bar", extensions=["wikilinks"])
    assert result == '<p>foo  bar</p>'
