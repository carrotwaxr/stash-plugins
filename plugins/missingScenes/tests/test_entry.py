"""entry.py: a scene update that didn't set stash_ids is answered before missing_scenes loads.

Stash runs post hooks one scene at a time before the mutation returns, so a bulk scene
update waits on this process for every scene.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

import entry

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIPPED = {"output": {"success": True, "message": "stash_ids not changed"}}
HEAVY = ("missing_scenes", "stashbox_api", "theporndb_api", "fingerprint_index", "sqlite3", "ssl", "urllib.request")


def hook(fields, hook_type="Scene.Update.Post"):
    ctx = {"type": hook_type, "id": 5, "input": {"ids": ["5"]}}
    if fields is not None:
        ctx["inputFields"] = list(fields)
    return {"server_connection": {"Dir": "/nonexistent"}, "args": {"hookContext": ctx}}


def run(monkeypatch, capsys, input_data):
    """entry.run() with missing_scenes.main recorded; returns (reply, inputs main got)."""
    import missing_scenes
    got = []
    monkeypatch.setattr(missing_scenes, "main", lambda data=None: got.append(data) or print('{"output": "main"}'))
    entry.run(json.dumps(input_data) if not isinstance(input_data, str) else input_data)
    return json.loads(capsys.readouterr().out), got


@pytest.mark.parametrize("fields", [["ids", "group_ids"], ["id", "title", "rating100"], ["id"], [], None])
def test_update_without_stash_ids_is_answered_without_main(monkeypatch, capsys, fields):
    reply, got = run(monkeypatch, capsys, hook(fields))
    assert reply == SKIPPED
    assert got == []


@pytest.mark.parametrize("input_data", [
    hook(["id", "stash_ids"]),
    hook(["ids", "group_ids", "stash_ids"]),
    hook(None, "Scene.Destroy.Post"),
    {"server_connection": {}, "args": {"mode": "scan"}},
    {"server_connection": {}, "args": {"operation": "find_missing", "entity_type": "performer"}},
    {"server_connection": {}, "args": {}},
])
def test_everything_else_goes_to_main_with_the_parsed_input(monkeypatch, capsys, input_data):
    reply, got = run(monkeypatch, capsys, input_data)
    assert reply == {"output": "main"}
    assert got == [input_data]


def test_invalid_json_is_an_error_reply(monkeypatch, capsys):
    reply, got = run(monkeypatch, capsys, "{not json")
    assert reply["error"].startswith("Invalid JSON input")
    assert got == []


def test_the_skip_loads_nothing_heavy():
    """The real script, run as Stash runs it: the skip imports none of the plugin's modules."""
    proc = subprocess.run(
        [sys.executable, "-X", "importtime", os.path.join(PLUGIN_DIR, "entry.py")],
        input=json.dumps(hook(["ids", "group_ids"])), capture_output=True, text=True, timeout=30,
    )
    assert json.loads(proc.stdout) == SKIPPED
    imported = {line.rsplit("|", 1)[-1].strip() for line in proc.stderr.splitlines() if "|" in line}
    assert not imported & set(HEAVY), imported & set(HEAVY)


def test_the_manifest_runs_entry():
    with open(os.path.join(PLUGIN_DIR, "missingScenes.yml"), encoding="utf-8") as f:
        manifest = yaml.safe_load(f)
    assert manifest["exec"] == ["python", "{pluginDir}/entry.py"]


def test_the_hook_handler_uses_the_same_rule():
    import missing_scenes
    assert missing_scenes.sets_stash_ids is entry.sets_stash_ids
