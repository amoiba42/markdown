import unittest

import markdown


class TestWikiLinkLabel(unittest.TestCase):
    def test_label_is_shown(self):
        out = markdown.markdown("[[Target|Label]]", extensions=["wikilinks"])
        self.assertIn(">Label</a>", out)
