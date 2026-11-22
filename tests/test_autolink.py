import markdown
import pytest

def test_ac1_tel_link_in_list():
    """
    Given markdown text with a list containing a tel scheme autolink such as - <tel:+123>,
    when parsed with markdown.markdown(), then it returns HTML where the tel autolink is
    converted into an anchor tag <li><a href="tel:+123">tel:+123</a></li>.
    """
    result = markdown.markdown("- <tel:+123>")
    expected = '<ul>\n<li><a href="tel:+123">tel:+123</a></li>\n</ul>'
    assert result == expected

def test_ac2_tel_link_in_emphasis():
    """
    Given markdown text with emphasis containing a tel scheme autolink such as *call <tel:+123>*,
    when parsed with markdown.markdown(), then it returns HTML where the tel autolink inside
    emphasis is converted into an anchor tag <p><em>call <a href="tel:+123">tel:+123</a></em></p>.
    """
    result = markdown.markdown("*call <tel:+123>*")
    expected = '<p><em>call <a href="tel:+123">tel:+123</a></em></p>'
    assert result == expected

def test_ac3_tel_link_in_code_span():
    """
    Given markdown text with a code span containing a tel scheme autolink `<tel:+123>`,
    when parsed with markdown.markdown(), then it returns HTML where the code span remains
    code without being converted to an autolink tag.
    """
    result = markdown.markdown("`<tel:+123>`")
    expected = '<p><code>&lt;tel:+123&gt;</code></p>'
    assert result == expected
