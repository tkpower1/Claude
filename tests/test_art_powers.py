"""Tests for ArtPowers module."""

import pytest
from superpowers import ArtPowers


class TestGetPattern:
    """Tests for get_pattern method."""

    def test_star_pattern(self):
        result = ArtPowers.get_pattern("star")
        assert "*" in result
        assert "\n" in result

    def test_heart_pattern(self):
        result = ArtPowers.get_pattern("heart")
        assert "*" in result

    def test_unknown_pattern(self):
        with pytest.raises(ValueError, match="Unknown pattern"):
            ArtPowers.get_pattern("nonexistent")


class TestListPatterns:
    """Tests for list_patterns method."""

    def test_returns_list(self):
        result = ArtPowers.list_patterns()
        assert isinstance(result, list)
        assert "star" in result
        assert "heart" in result
        assert "rocket" in result


class TestBanner:
    """Tests for banner method."""

    def test_basic_banner(self):
        result = ArtPowers.banner("Hello")
        lines = result.split("\n")
        assert len(lines) == 3
        assert "Hello" in lines[1]

    def test_custom_char(self):
        result = ArtPowers.banner("Hi", char="#")
        assert "#" in result
        assert "*" not in result


class TestBox:
    """Tests for box method."""

    def test_single_style(self):
        result = ArtPowers.box("Test")
        assert "+" in result
        assert "-" in result
        assert "|" in result

    def test_multiline_text(self):
        result = ArtPowers.box("Line1\nLine2")
        lines = result.split("\n")
        assert len(lines) == 4  # top border, 2 content lines, bottom border


class TestBigText:
    """Tests for big_text method."""

    def test_single_letter(self):
        result = ArtPowers.big_text("A")
        assert "A" in result
        lines = result.split("\n")
        assert len(lines) == 5

    def test_word(self):
        result = ArtPowers.big_text("HI")
        assert "H" in result
        assert "I" in result

    def test_lowercase_converted(self):
        result = ArtPowers.big_text("a")
        assert "A" in result


class TestProgressBar:
    """Tests for progress_bar method."""

    def test_zero_progress(self):
        result = ArtPowers.progress_bar(0, 100)
        assert "0.0%" in result
        assert "░" in result

    def test_full_progress(self):
        result = ArtPowers.progress_bar(100, 100)
        assert "100.0%" in result
        assert "█" in result

    def test_half_progress(self):
        result = ArtPowers.progress_bar(50, 100, width=20)
        assert "50.0%" in result

    def test_over_progress(self):
        result = ArtPowers.progress_bar(150, 100)
        assert "100.0%" in result
