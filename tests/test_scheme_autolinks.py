import unittest

import markdown


class TestSchemeAutolinks(unittest.TestCase):
    def test_tel_in_list(self):
        self.assertEqual(markdown.markdown("- <tel:+123>"),
                         '<ul>\n<li><a href="tel:+123">tel:+123</a></li>\n</ul>')

    def test_tel_in_emphasis(self):
        self.assertEqual(markdown.markdown("*call <tel:+123>*"),
                         '<p><em>call <a href="tel:+123">tel:+123</a></em></p>')

    def test_code_span_unchanged(self):
        self.assertEqual(markdown.markdown("`<tel:+123>`"), "<p><code>&lt;tel:+123&gt;</code></p>")

    def test_skype_with_at_sign_is_not_mail(self):
        self.assertEqual(markdown.markdown("<skype:user@example.com>"),
                         '<p><a href="skype:user@example.com">skype:user@example.com</a></p>')
