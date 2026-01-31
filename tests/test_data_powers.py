"""Tests for DataPowers module."""

import pytest
from superpowers import DataPowers


class TestJsonToCsv:
    """Tests for json_to_csv method."""

    def test_basic_conversion(self):
        data = [
            {"name": "Alice", "age": "30"},
            {"name": "Bob", "age": "25"}
        ]
        result = DataPowers.json_to_csv(data)
        assert "name,age" in result
        assert "Alice,30" in result
        assert "Bob,25" in result

    def test_empty_list(self):
        assert DataPowers.json_to_csv([]) == ""


class TestCsvToJson:
    """Tests for csv_to_json method."""

    def test_basic_conversion(self):
        csv_data = "name,age\nAlice,30\nBob,25"
        result = DataPowers.csv_to_json(csv_data)
        assert len(result) == 2
        assert result[0]["name"] == "Alice"
        assert result[1]["age"] == "25"


class TestFlattenDict:
    """Tests for flatten_dict method."""

    def test_basic_flatten(self):
        data = {"a": {"b": {"c": 1}}}
        result = DataPowers.flatten_dict(data)
        assert result == {"a.b.c": 1}

    def test_mixed_nesting(self):
        data = {"a": 1, "b": {"c": 2, "d": {"e": 3}}}
        result = DataPowers.flatten_dict(data)
        assert result == {"a": 1, "b.c": 2, "b.d.e": 3}

    def test_custom_separator(self):
        data = {"a": {"b": 1}}
        result = DataPowers.flatten_dict(data, separator="_")
        assert result == {"a_b": 1}


class TestUnflattenDict:
    """Tests for unflatten_dict method."""

    def test_basic_unflatten(self):
        data = {"a.b.c": 1}
        result = DataPowers.unflatten_dict(data)
        assert result == {"a": {"b": {"c": 1}}}

    def test_custom_separator(self):
        data = {"a_b": 1}
        result = DataPowers.unflatten_dict(data, separator="_")
        assert result == {"a": {"b": 1}}


class TestDeepMerge:
    """Tests for deep_merge method."""

    def test_basic_merge(self):
        dict1 = {"a": 1, "b": 2}
        dict2 = {"b": 3, "c": 4}
        result = DataPowers.deep_merge(dict1, dict2)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        dict1 = {"a": {"b": 1, "c": 2}}
        dict2 = {"a": {"c": 3, "d": 4}}
        result = DataPowers.deep_merge(dict1, dict2)
        assert result == {"a": {"b": 1, "c": 3, "d": 4}}


class TestGroupBy:
    """Tests for group_by method."""

    def test_basic_grouping(self):
        data = [
            {"type": "fruit", "name": "apple"},
            {"type": "fruit", "name": "banana"},
            {"type": "veggie", "name": "carrot"}
        ]
        result = DataPowers.group_by(data, "type")
        assert len(result["fruit"]) == 2
        assert len(result["veggie"]) == 1


class TestTransformKeys:
    """Tests for transform_keys method."""

    def test_to_snake_case(self):
        data = {"firstName": "John", "lastName": "Doe"}
        result = DataPowers.transform_keys(data, "snake_case")
        assert result == {"first_name": "John", "last_name": "Doe"}

    def test_to_camel_case(self):
        data = {"first_name": "John", "last_name": "Doe"}
        result = DataPowers.transform_keys(data, "camelCase")
        assert result == {"firstName": "John", "lastName": "Doe"}

    def test_to_pascal_case(self):
        data = {"first_name": "John"}
        result = DataPowers.transform_keys(data, "PascalCase")
        assert result == {"FirstName": "John"}
