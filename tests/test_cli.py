"""Tests for CLI module."""

import pytest
from superpowers.cli import main, create_parser


class TestCLI:
    """Tests for the CLI interface."""

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0

    def test_no_command_shows_help(self, capsys):
        result = main([])
        assert result == 0

    def test_text_analyze(self, capsys):
        result = main(["text", "analyze", "Hello world"])
        assert result == 0
        captured = capsys.readouterr()
        assert "word_count" in captured.out

    def test_text_frequency(self, capsys):
        result = main(["text", "frequency", "the the the cat"])
        assert result == 0
        captured = capsys.readouterr()
        assert "the: 3" in captured.out

    def test_text_reverse(self, capsys):
        result = main(["text", "reverse", "hello world"])
        assert result == 0
        captured = capsys.readouterr()
        assert "olleh dlrow" in captured.out

    def test_text_leet(self, capsys):
        result = main(["text", "leet", "leet"])
        assert result == 0
        captured = capsys.readouterr()
        assert "1337" in captured.out

    def test_text_cipher(self, capsys):
        result = main(["text", "cipher", "abc", "--shift", "3"])
        assert result == 0
        captured = capsys.readouterr()
        assert "def" in captured.out

    def test_code_class(self, capsys):
        result = main(["code", "class", "User", "--attrs", "name,email"])
        assert result == 0
        captured = capsys.readouterr()
        assert "class User:" in captured.out

    def test_code_function(self, capsys):
        result = main(["code", "function", "calculate", "--params", "a,b"])
        assert result == 0
        captured = capsys.readouterr()
        assert "def calculate" in captured.out

    def test_data_flatten(self, capsys):
        result = main(["data", "flatten", '{"a": {"b": 1}}'])
        assert result == 0
        captured = capsys.readouterr()
        assert "a.b" in captured.out

    def test_data_invalid_json(self, capsys):
        result = main(["data", "flatten", "not-json"])
        assert result == 1

    def test_art_pattern(self, capsys):
        result = main(["art", "pattern", "star"])
        assert result == 0
        captured = capsys.readouterr()
        assert "*" in captured.out

    def test_art_list(self, capsys):
        result = main(["art", "list"])
        assert result == 0
        captured = capsys.readouterr()
        assert "star" in captured.out

    def test_art_banner(self, capsys):
        result = main(["art", "banner", "Hello"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Hello" in captured.out

    def test_art_big(self, capsys):
        result = main(["art", "big", "HI"])
        assert result == 0

    def test_art_progress(self, capsys):
        result = main(["art", "progress", "50", "100"])
        assert result == 0
        captured = capsys.readouterr()
        assert "50.0%" in captured.out
