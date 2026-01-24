"""Data manipulation and transformation superpowers."""

import json
import csv
import io
from typing import List, Dict, Any, Optional


class DataPowers:
    """A collection of data manipulation utilities."""

    @staticmethod
    def json_to_csv(json_data: List[Dict[str, Any]]) -> str:
        """
        Convert a list of dictionaries to CSV format.

        Args:
            json_data: List of dictionaries with consistent keys

        Returns:
            CSV formatted string
        """
        if not json_data:
            return ""

        output = io.StringIO()
        fieldnames = list(json_data[0].keys())
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(json_data)

        return output.getvalue()

    @staticmethod
    def csv_to_json(csv_data: str) -> List[Dict[str, str]]:
        """
        Convert CSV formatted string to list of dictionaries.

        Args:
            csv_data: CSV formatted string with headers

        Returns:
            List of dictionaries
        """
        reader = csv.DictReader(io.StringIO(csv_data))
        return list(reader)

    @staticmethod
    def flatten_dict(
        data: Dict[str, Any],
        separator: str = ".",
        prefix: str = ""
    ) -> Dict[str, Any]:
        """
        Flatten a nested dictionary.

        Args:
            data: Nested dictionary to flatten
            separator: Character to use between nested keys
            prefix: Prefix for keys (used in recursion)

        Returns:
            Flattened dictionary
        """
        result = {}
        for key, value in data.items():
            new_key = f"{prefix}{separator}{key}" if prefix else key
            if isinstance(value, dict):
                result.update(DataPowers.flatten_dict(value, separator, new_key))
            else:
                result[new_key] = value
        return result

    @staticmethod
    def unflatten_dict(data: Dict[str, Any], separator: str = ".") -> Dict[str, Any]:
        """
        Unflatten a dictionary with separated keys back to nested form.

        Args:
            data: Flattened dictionary
            separator: Character used between nested keys

        Returns:
            Nested dictionary
        """
        result: Dict[str, Any] = {}
        for key, value in data.items():
            parts = key.split(separator)
            current = result
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value
        return result

    @staticmethod
    def deep_merge(dict1: Dict[str, Any], dict2: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep merge two dictionaries.

        Values from dict2 override values from dict1.
        Nested dictionaries are merged recursively.
        """
        result = dict1.copy()
        for key, value in dict2.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = DataPowers.deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    @staticmethod
    def group_by(data: List[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Group a list of dictionaries by a specified key.

        Args:
            data: List of dictionaries
            key: Key to group by

        Returns:
            Dictionary mapping key values to lists of matching items
        """
        result: Dict[str, List[Dict[str, Any]]] = {}
        for item in data:
            group_key = str(item.get(key, "undefined"))
            if group_key not in result:
                result[group_key] = []
            result[group_key].append(item)
        return result

    @staticmethod
    def transform_keys(
        data: Dict[str, Any],
        transform: str = "snake_case"
    ) -> Dict[str, Any]:
        """
        Transform dictionary keys to specified case.

        Args:
            data: Dictionary to transform
            transform: Target case ('snake_case', 'camelCase', 'PascalCase')

        Returns:
            Dictionary with transformed keys
        """
        def to_snake_case(s: str) -> str:
            import re
            s = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', s)
            return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s).lower()

        def to_camel_case(s: str) -> str:
            parts = s.replace('-', '_').split('_')
            return parts[0].lower() + ''.join(p.capitalize() for p in parts[1:])

        def to_pascal_case(s: str) -> str:
            parts = s.replace('-', '_').split('_')
            return ''.join(p.capitalize() for p in parts)

        transformers = {
            "snake_case": to_snake_case,
            "camelCase": to_camel_case,
            "PascalCase": to_pascal_case,
        }

        if transform not in transformers:
            raise ValueError(f"Unknown transform: {transform}")

        transformer = transformers[transform]
        return {transformer(k): v for k, v in data.items()}
