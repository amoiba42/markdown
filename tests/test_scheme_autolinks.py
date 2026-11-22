import unittest

import markdown


class TestSchemeAutolinks(unittest.TestCase):
    def test_tel(self):
        self.assertIn("tel", markdown.markdown("- <tel:+123>"))
