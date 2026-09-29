#!/usr/bin/env python3
"""Check every plugin manifest against what Stash and build_site.sh expect.

Run from the repo root: python .github/scripts/lint_manifests.py
"""

import os
import re
import sys

import yaml

SETTING_TYPES = {"STRING", "NUMBER", "BOOLEAN"}
INTERFACES = {"rpc", "raw", "js"}


def lint(path):
    """Return a list of problems with one manifest."""
    plugin_dir = os.path.dirname(path)
    plugin_id = os.path.splitext(os.path.basename(path))[0]
    problems = []

    def local_file(rel, what):
        if not os.path.isfile(os.path.join(plugin_dir, rel)):
            problems.append(f"{what} not found: {rel}")

    try:
        with open(path, encoding="utf-8") as f:
            m = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"invalid YAML: {e}"]
    if not isinstance(m, dict):
        return ["manifest is not a mapping"]

    # Stash uses the file name as the plugin id; build_site.sh zips the directory
    if os.path.basename(plugin_dir) != plugin_id:
        problems.append(f"file name {plugin_id}.yml does not match its directory")

    for key in ("name", "description", "version"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            problems.append(f"missing or empty {key}")
    if isinstance(m.get("version"), str) and not re.fullmatch(r"\d+\.\d+\.\d+", m["version"]):
        problems.append(f"version {m['version']!r} is not MAJOR.MINOR.PATCH")

    if "interface" in m and m["interface"] not in INTERFACES:
        problems.append(f"interface must be one of {sorted(INTERFACES)}")

    settings = m.get("settings")
    if settings is not None:
        if not isinstance(settings, dict):
            problems.append("settings must be a map of setting id to definition, not a list")
        else:
            for key, setting in settings.items():
                if not isinstance(setting, dict):
                    problems.append(f"setting {key} must be a map")
                    continue
                if setting.get("type") not in SETTING_TYPES:
                    problems.append(f"setting {key}: type must be one of {sorted(SETTING_TYPES)}")
                if not setting.get("displayName"):
                    problems.append(f"setting {key}: missing displayName")

    ui = m.get("ui") or {}
    for kind in ("javascript", "css"):
        for rel in ui.get(kind) or []:
            if not re.match(r"https?://", rel):
                local_file(rel, f"ui.{kind} file")
    for prefix, rel in (ui.get("assets") or {}).items():
        target = os.path.normpath(os.path.join(plugin_dir, rel))
        if target == os.path.normpath(plugin_dir):
            problems.append(f"ui.assets {prefix} serves the whole plugin directory; use a subfolder")
        elif not os.path.isdir(target):
            problems.append(f"ui.assets directory not found: {rel}")

    for arg in m.get("exec") or []:
        if isinstance(arg, str) and arg.startswith("{pluginDir}/"):
            local_file(arg[len("{pluginDir}/"):], "exec script")

    for kind in ("tasks", "hooks"):
        for op in m.get(kind) or []:
            for arg, value in (op.get("defaultArgs") or {}).items():
                if not isinstance(value, str):
                    problems.append(f"{kind} {op.get('name')!r}: defaultArgs.{arg} must be a string")

    return problems


def main():
    manifests = []
    for root, _dirs, files in os.walk("plugins"):
        manifests += [os.path.join(root, f) for f in files if f.endswith(".yml")]
    if not manifests:
        print("no manifests found; run from the repo root")
        return 1

    failed = 0
    for path in sorted(manifests):
        problems = lint(path)
        # build_site.sh treats every .yml under plugins/ as a manifest
        if os.path.dirname(os.path.dirname(path)) != "plugins":
            problems.append("manifest is not directly inside plugins/<id>/")
        status = "ok" if not problems else "FAIL"
        print(f"{status:4} {path}")
        for p in problems:
            print(f"     - {p}")
        failed += bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
