"""
Tests for TOC extension with non-English (Unicode) headings.
"""

import pytest
from markdown import markdown


class TestTOCUnicode:
    """Tests for TOC behavior with non-English headings."""

    def test_ac1_chinese_headings_generate_meaningful_ids(self):
        """
        AC-1: Given markdown text with non-English headings (such as Chinese characters)
        and the [TOC] marker, when markdown.markdown is called with the toc extension,
        then the generated table of contents links and heading IDs should be derived
        from the heading text rather than generic fallback anchors (e.g. _1, _2).
        """
        # Chinese for "Hello" and "World"
        md_text = "[TOC]\n\n# 你好\n\n# 世界"
        result = markdown(md_text, extensions=['markdown.extensions.toc'])
        
        # The TOC should contain links with hrefs derived from the Chinese characters
        # Not generic fallback anchors like #_1, #_2
        assert 'href="#_1"' not in result, "Should not contain generic fallback anchor #_1"
        assert 'href="#_2"' not in result, "Should not contain generic fallback anchor #_2"
        assert 'id="_1"' not in result, "Should not contain generic fallback id _1"
        assert 'id="_2"' not in result, "Should not contain generic fallback id _2"
        
        # The IDs should be derived from the Chinese text
        # The exact format depends on the slugify implementation, but should not be generic
        assert '你好' in result or 'ni-hao' in result or 'nihao' in result, \
            "TOC or headings should contain some representation of the Chinese text"
        assert '世界' in result or 'shi-jie' in result or 'shijie' in result, \
            "TOC or headings should contain some representation of the Chinese text"

    def test_ac2_english_headings_unchanged_behavior(self):
        """
        AC-2: Given markdown text with English headings and the [TOC] marker,
        when markdown.markdown is called with the toc extension,
        then existing English slugification behavior remains unchanged.
        """
        md_text = "[TOC]\n\n# Hello World"
        result = markdown(md_text, extensions=['markdown.extensions.toc'])
        
        # English headings should continue to work as before
        assert 'href="#hello-world"' in result, "TOC link should have href='#hello-world'"
        assert 'id="hello-world"' in result, "Heading should have id='hello-world'"

    def test_chinese_headings_without_toc_marker(self):
        """Test that Chinese headings get proper IDs even without [TOC] marker."""
        md_text = "# 你好\n\n# 世界"
        result = markdown(md_text, extensions=['markdown.extensions.toc'])
        
        # Headings should have IDs derived from Chinese text, not generic fallbacks
        assert 'id="_1"' not in result, "Should not contain generic fallback id _1"
        assert 'id="_2"' not in result, "Should not contain generic fallback id _2"

    def test_mixed_english_and_chinese_headings(self):
        """Test mixed English and Chinese headings."""
        md_text = "[TOC]\n\n# Hello\n\n# 世界\n\n# World"
        result = markdown(md_text, extensions=['markdown.extensions.toc'])
        
        # English headings should work as before
        assert 'href="#hello"' in result
        assert 'id="hello"' in result
        assert 'href="#world"' in result
        assert 'id="world"' in result
        
        # Chinese heading should not get generic fallback
        assert 'href="#_1"' not in result
        assert 'href="#_2"' not in result
        assert 'href="#_3"' not in result
        assert 'id="_1"' not in result
        assert 'id="_2"' not in result
        assert 'id="_3"' not in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
