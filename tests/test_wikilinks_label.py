import unittest

import markdown


class TestWikiLinkLabel(unittest.TestCase):
    def test_label_is_link_text_and_target_is_href(self):
        out = markdown.markdown("[[Target|Label]]", extensions=["wikilinks"])
        self.assertEqual(out, '<p><a class="wikilink" href="/Target/">Label</a></p>')

    def test_without_label_unchanged(self):
        out = markdown.markdown("[[Target]]", extensions=["wikilinks"])
        self.assertEqual(out, '<p><a class="wikilink" href="/Target/">Target</a></p>')
