import markdown


def test_tel_autolink_in_list():
    assert markdown.markdown("- <tel:+123>") == '<ul>\n<li><a href="tel:+123">tel:+123</a></li>\n</ul>'


def test_tel_autolink_in_emphasis():
    assert markdown.markdown("*call <tel:+123>*") == '<p><em>call <a href="tel:+123">tel:+123</a></em></p>'


def test_tel_autolink_in_code_span():
    assert markdown.markdown("`<tel:+123>`") == '<p><code>&lt;tel:+123&gt;</code></p>'
