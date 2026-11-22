import markdown
import pytest

def test_ac1_tel_autolink_in_list():
    """
    AC-1: Given markdown text with a list containing a tel scheme autolink 
    such as `- <tel:+123>`, when parsed with markdown.markdown(), then it 
    returns HTML where the tel autolink is converted into an anchor tag 
    <li><a href="tel:+123">tel:+123</a></li>.
    """
    result = markdown.markdown("- <tel:+123>")
    assert result == '<ul>\n<li><a href="tel:+123">tel:+123</a></li>\n</ul>'

def test_ac2_tel_autolink_in_emphasis():
    """
    AC-2: Given markdown text with emphasis containing a tel scheme autolink 
    such as `*call <tel:+123>*`, when parsed with markdown.markdown(), then 
    it returns HTML where the tel autolink inside emphasis is converted 
    into an anchor tag <p><em>call <a href="tel:+123">tel:+123</a></em></p>.
    """
    result = markdown.markdown("*call <tel:+123>*")
    assert result == '<p><em>call <a href="tel:+123">tel:+123</a></em></p>'

def test_ac3_tel_autolink_in_code_span():
    """
    AC-3: Given markdown text with a code span containing a tel scheme 
    autolink `` `<tel:+123>` ``, when parsed with markdown.markdown(), then 
    it returns HTML where the code span remains code without being 
    converted to an autolink tag.
    """
    result = markdown.markdown("`<tel:+123>`")
    assert result == '<p><code>&lt;tel:+123&gt;</code></p>'
