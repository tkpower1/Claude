"""Tests for CodePowers module."""

import pytest
from superpowers import CodePowers


class TestGenerateClass:
    """Tests for generate_class method."""

    def test_python_class_basic(self):
        result = CodePowers.generate_class("User", ["name", "email"])
        assert "class User:" in result
        assert "def __init__(self, name, email):" in result
        assert "self.name = name" in result
        assert "self.email = email" in result

    def test_python_class_with_methods(self):
        result = CodePowers.generate_class("User", ["name"], methods=["save", "delete"])
        assert "def save(self):" in result
        assert "def delete(self):" in result

    def test_javascript_class(self):
        result = CodePowers.generate_class("User", ["name"], language="javascript")
        assert "class User {" in result
        assert "constructor(name)" in result
        assert "this.name = name;" in result

    def test_typescript_class(self):
        result = CodePowers.generate_class("User", ["name"], language="typescript")
        assert "class User {" in result
        assert "private name: any;" in result
        assert "constructor(name: any)" in result

    def test_unsupported_language(self):
        with pytest.raises(ValueError, match="Unsupported language"):
            CodePowers.generate_class("User", ["name"], language="ruby")


class TestGenerateFunction:
    """Tests for generate_function method."""

    def test_python_function(self):
        result = CodePowers.generate_function("calculate", ["a", "b"])
        assert "def calculate(a, b)" in result
        assert "raise NotImplementedError" in result

    def test_javascript_function(self):
        result = CodePowers.generate_function("calculate", ["a", "b"], language="javascript")
        assert "function calculate(a, b)" in result
        assert "throw new Error" in result


class TestCountLines:
    """Tests for count_lines method."""

    def test_basic_count(self):
        code = """def hello():
    # A comment
    print("Hello")

# Another comment
"""
        result = CodePowers.count_lines(code)
        assert result["total_lines"] == 6
        assert result["blank_lines"] == 1
        assert result["comment_lines"] == 2
        assert result["code_lines"] == 3

    def test_empty_code(self):
        result = CodePowers.count_lines("")
        assert result["total_lines"] == 1  # Empty string has 1 line
        assert result["blank_lines"] == 1
