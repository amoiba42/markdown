import unittest

import markdown


class TestSchemeAutolinks(unittest.TestCase):
    def test_tel_in_list(self):
        self.assertEqual(markdown.markdown("- <tel:+123>"),
                         '<ul>\n<li><a href="tel:+123">tel:+123</a></li>\n</ul>')

    def test_tel_in_code_span_unchanged(self):
        self.assertEqual(markdown.markdown("`<tel:+123>`"), "<p><code>&lt;tel:+123&gt;</code></p>")
