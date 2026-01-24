"""Tests for TextPowers module."""

import pytest
from superpowers import TextPowers


class TestWordFrequency:
    """Tests for word_frequency method."""

    def test_basic_frequency(self):
        text = "the quick brown fox jumps over the lazy dog the"
        result = TextPowers.word_frequency(text, top_n=3)
        assert result[0] == ("the", 3)
        assert len(result) == 3

    def test_empty_text(self):
        result = TextPowers.word_frequency("", top_n=5)
        assert result == []

    def test_single_word(self):
        result = TextPowers.word_frequency("hello", top_n=10)
        assert result == [("hello", 1)]

    def test_case_insensitive(self):
        result = TextPowers.word_frequency("Hello HELLO hello", top_n=1)
        assert result == [("hello", 3)]


class TestReverseWords:
    """Tests for reverse_words method."""

    def test_basic_reverse(self):
        assert TextPowers.reverse_words("hello world") == "olleh dlrow"

    def test_single_word(self):
        assert TextPowers.reverse_words("hello") == "olleh"

    def test_empty_string(self):
        assert TextPowers.reverse_words("") == ""

    def test_preserves_spaces(self):
        assert TextPowers.reverse_words("a b c") == "a b c"


class TestLeetspeak:
    """Tests for to_leetspeak method."""

    def test_basic_leet(self):
        assert TextPowers.to_leetspeak("leet") == "1337"

    def test_preserves_case_for_non_leet(self):
        assert TextPowers.to_leetspeak("Hello") == "H3110"

    def test_empty_string(self):
        assert TextPowers.to_leetspeak("") == ""

    def test_no_leet_chars(self):
        assert TextPowers.to_leetspeak("xyz") == "xyz"


class TestAnalyzeText:
    """Tests for analyze_text method."""

    def test_basic_analysis(self):
        text = "Hello world. How are you?"
        result = TextPowers.analyze_text(text)
        assert result["word_count"] == 5
        assert result["sentence_count"] == 2
        assert "character_count" in result
        assert "average_word_length" in result

    def test_empty_text(self):
        result = TextPowers.analyze_text("")
        assert result["word_count"] == 0
        assert result["character_count"] == 0


class TestPalindromeCheck:
    """Tests for palindrome_check method."""

    def test_simple_palindrome(self):
        assert TextPowers.palindrome_check("racecar") is True

    def test_palindrome_with_spaces(self):
        assert TextPowers.palindrome_check("A man a plan a canal Panama") is True

    def test_not_palindrome(self):
        assert TextPowers.palindrome_check("hello") is False

    def test_empty_string(self):
        assert TextPowers.palindrome_check("") is True


class TestCaesarCipher:
    """Tests for caesar_cipher method."""

    def test_basic_cipher(self):
        assert TextPowers.caesar_cipher("abc", shift=3) == "def"

    def test_wrap_around(self):
        assert TextPowers.caesar_cipher("xyz", shift=3) == "abc"

    def test_preserves_case(self):
        assert TextPowers.caesar_cipher("ABC", shift=1) == "BCD"

    def test_preserves_non_alpha(self):
        assert TextPowers.caesar_cipher("a1b2", shift=1) == "b1c2"

    def test_negative_shift(self):
        assert TextPowers.caesar_cipher("def", shift=-3) == "abc"
