"""
Tests for shared default_settings.json integrity.

Requires pyyaml:
  python -m pip install pyyaml

Run:
  python -m unittest tests.test_default_settings_file
"""
import json
import os
import unittest
import yaml


def _defaults_path():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    plugin_dir = os.path.dirname(tests_dir)
    return os.path.join(plugin_dir, "default_settings.json")


def _plugin_yml_path():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    plugin_dir = os.path.dirname(tests_dir)
    return os.path.join(plugin_dir, "tagManager.yml")


def _format_json_error(path, err: json.JSONDecodeError) -> str:
    """Single readable block for invalid JSON (avoids long decoder tracebacks)."""
    doc = err.doc or ""
    start = max(0, err.pos - 50)
    end = min(len(doc), err.pos + 50)
    snippet = doc[start:end].replace("\n", "\\n")
    pointer = " " * max(0, err.pos - start) + "^"
    return (
        f"Invalid JSON in {path}\n"
        f"  Line {err.lineno}, column {err.colno}: {err.msg}\n"
        f"  Context: ...{snippet!s}...\n"
        f"           {pointer}"
    )


class TestDefaultSettingsFile(unittest.TestCase):
    """Validate default_settings.json shape and value types."""

    maxDiff = None

    @staticmethod
    def _raise_parse_error(msg: str) -> None:
        """Fail the test without chaining the original parser exception (cleaner unittest output)."""
        raise AssertionError(msg) from None

    def _load_defaults(self):
        path = _defaults_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            self._raise_parse_error(f"Cannot read default settings file:\n  {path}\n  {e}")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            self._raise_parse_error(_format_json_error(path, e))

        if not isinstance(data, dict):
            self.fail(
                f"default_settings.json must be a JSON object {{ ... }}, got {type(data).__name__}\n"
                f"  File: {path}"
            )
        return data

    def _load_settings_schema_from_plugin_yml(self):
        path = _plugin_yml_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                plugin_cfg = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            self._raise_parse_error(f"Invalid YAML in tagManager.yml:\n  {path}\n  {e}")
        except OSError as e:
            self._raise_parse_error(f"Cannot read tagManager.yml:\n  {path}\n  {e}")

        settings = plugin_cfg.get("settings") or {}
        if not isinstance(settings, dict):
            self.fail(f"tagManager.yml 'settings' must be a mapping, got {type(settings).__name__}")

        schema = {}
        for key, meta in settings.items():
            if not isinstance(meta, dict):
                self.fail(
                    f"tagManager.yml settings.{key} must be a mapping with a 'type' field, "
                    f"got {type(meta).__name__}"
                )
            t = meta.get("type")
            if not t:
                self.fail(f"tagManager.yml settings.{key} is missing 'type'")
            schema[key] = t
        return schema

    def test_default_settings_is_well_formed(self):
        """Shared defaults file should contain all required keys with expected types."""
        defaults = self._load_defaults()
        settings_schema = self._load_settings_schema_from_plugin_yml()

        required_keys = set(settings_schema.keys())
        actual_keys = set(defaults.keys())
        if actual_keys != required_keys:
            missing = sorted(required_keys - actual_keys)
            extra = sorted(actual_keys - required_keys)
            lines = ["default_settings.json keys must match tagManager.yml settings exactly."]
            if missing:
                lines.append(f"  Missing keys (add to default_settings.json): {missing}")
            if extra:
                lines.append(f"  Extra keys (remove or add to tagManager.yml): {extra}")
            lines.append(f"  Expected ({len(required_keys)}): {sorted(required_keys)}")
            lines.append(f"  Actual   ({len(actual_keys)}): {sorted(actual_keys)}")
            self.fail("\n".join(lines))

        expected_python_type_by_setting_type = {
            "BOOLEAN": bool,
            "NUMBER": int,
            "STRING": str,
        }

        type_errors = []
        for key, setting_type in settings_schema.items():
            if setting_type not in expected_python_type_by_setting_type:
                type_errors.append(
                    f"  {key}: unknown type {setting_type!r} in tagManager.yml "
                    f"(expected BOOLEAN, NUMBER, or STRING)"
                )
                continue
            expected_py = expected_python_type_by_setting_type[setting_type]
            value = defaults[key]
            if not isinstance(value, expected_py):
                type_errors.append(
                    f"  {key}: expected {setting_type} ({expected_py.__name__}), "
                    f"got {type(value).__name__} ({value!r})"
                )

        if type_errors:
            self.fail(
                "default_settings.json value types do not match tagManager.yml:\n"
                + "\n".join(type_errors)
            )


if __name__ == "__main__":
    unittest.main()
