"""
Test custom label feature for wikilinks extension.

This tests the [[Target|Label]] syntax where the URL comes from the target
part and the visible text comes from the label part.
"""

import markdown


class TestWikilinksCustomLabel:
    """ Test wikilinks with custom labels. """

    def test_ac1_custom_label_wikilink(self):
        """
        AC-1: Given markdown text containing a custom label wikilink [[Release Notes|what is new]],
        when converted to html using the wikilinks extension,
        then it produces an anchor tag with href pointing to the target URL
        derived from the target part and link text equal to the label part.
        """
        result = markdown.markdown("[[Release Notes|what is new]]", extensions=["markdown.extensions.wikilinks"])
        # The target "Release Notes" becomes the URL, label "what is new" becomes the link text
        expected = '<p><a class="wikilink" href="/Release_Notes/">what is new</a></p>'
        assert result == expected

    def test_ac2_plain_wikilink_still_works(self):
        """
        AC-2: Given markdown text containing a plain wikilink [[Release Notes]],
        when converted to html using the wikilinks extension,
        then it produces an anchor tag with href pointing to the URL derived
        from the page name and link text equal to the page name, behaving exactly as before.
        """
        result = markdown.markdown("[[Release Notes]]", extensions=["markdown.extensions.wikilinks"])
        expected = '<p><a class="wikilink" href="/Release_Notes/">Release Notes</a></p>'
        assert result == expected

    def test_custom_label_with_spaces_in_target(self):
        """ Test custom label with spaces in the target part. """
        result = markdown.markdown("[[My Page|custom label]]", extensions=["markdown.extensions.wikilinks"])
        expected = '<p><a class="wikilink" href="/My_Page/">custom label</a></p>'
        assert result == expected

    def test_custom_label_with_special_chars_in_label(self):
        """ Test custom label with special characters in the label part. """
        result = markdown.markdown("[[Target|label with spaces and _underscores_]]", extensions=["markdown.extensions.wikilinks"])
        expected = '<p><a class="wikilink" href="/Target/">label with spaces and _underscores_</a></p>'
        assert result == expected

    def test_custom_label_multiple_in_paragraph(self):
        """ Test multiple custom label wikilinks in a paragraph. """
        result = markdown.markdown("See [[Page One|first]] and [[Page Two|second]] for details.", extensions=["markdown.extensions.wikilinks"])
        expected = '<p>See <a class="wikilink" href="/Page_One/">first</a> and <a class="wikilink" href="/Page_Two/">second</a> for details.</p>'
        assert result == expected
