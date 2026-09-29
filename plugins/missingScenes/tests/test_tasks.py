"""Task validation (the scan path) and settings that match their descriptions (F15). Offline."""

import inspect
import json
import os
import re

import pytest
import yaml

import missing_scenes as ms
import stashbox_api
from tests.test_whisparr_flows import ADD_SETTINGS, SID, STASHDB, install, whisparr

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOXES = [{"endpoint": "https://stashdb.org/graphql", "api_key": "k", "name": "StashDB"},
         {"endpoint": "https://fansdb.cc/graphql", "api_key": "k2", "name": "FansDB"}]


class FakeStash:
    def __init__(self, libraries=("/data",), scan_result="job-1", ui=None):
        self.libraries = list(libraries)
        self.scan_result = scan_result
        self.ui = ui if ui is not None else {}
        self.scans = []
        self.events = []

    def __call__(self, query, variables=None):
        if "metadataScan" in query:
            self.scans.append(variables["input"])
            self.events.append("scan")
            return {"metadataScan": self.scan_result}
        if "stashBoxes" in query:
            return {"configuration": {"general": {"stashBoxes": BOXES}}}
        return {"configuration": {"ui": self.ui,
                                  "general": {"stashes": [{"path": p} for p in self.libraries]}}}


@pytest.fixture
def scan(monkeypatch):
    stash = FakeStash()
    invalidated = []
    monkeypatch.setattr(ms, "stash_graphql", stash)
    monkeypatch.setattr(ms, "invalidate_cache", lambda ep: (invalidated.append(ep), stash.events.append("inv")))
    stash.invalidated = invalidated
    return stash


# ---- scan path validation ---------------------------------------------------------

def test_scan_path_inside_a_library_starts_the_scan(scan):
    res = ms.task_scan_for_new_scenes({"scanPath": "/data/unsorted"})
    assert res["success"] is True
    assert scan.scans[0]["paths"] == ["/data/unsorted"]


@pytest.mark.parametrize("path", ["/data", "/data/", "/data/a/../b", "/data//x/"])
def test_paths_are_compared_normalized(scan, path):
    assert ms.task_scan_for_new_scenes({"scanPath": path})["success"] is True


@pytest.mark.parametrize("path", ["/data2", "/data2/x", "/other", "/", "/dat", "/data/../etc"])
def test_scan_path_outside_every_library_is_rejected(scan, path):
    scan.libraries = ["/data", "/media/lib"]
    res = ms.task_scan_for_new_scenes({"scanPath": path})
    assert res["success"] is False
    assert scan.scans == [], "no scan was started"
    assert "/data" in res["message"] and "/media/lib" in res["message"], "names the library roots"
    assert path in res["message"]
    assert scan.invalidated == []


def test_multiple_paths_are_each_validated(scan):
    res = ms.task_scan_for_new_scenes({"scanPath": " /data/a ;; /data/b ; "})
    assert res["success"] is True
    assert scan.scans[0]["paths"] == ["/data/a", "/data/b"]


def test_one_bad_path_rejects_them_all(scan):
    res = ms.task_scan_for_new_scenes({"scanPath": "/data/a;/elsewhere"})
    assert res["success"] is False and "/elsewhere" in res["message"]
    assert scan.scans == []


@pytest.mark.parametrize("value", ["", "  ", " ; ;", None])
def test_no_usable_path_is_not_configured(scan, value):
    res = ms.task_scan_for_new_scenes({"scanPath": value})
    assert res["success"] is False and "not configured" in res["message"]
    assert scan.scans == []


def test_no_libraries_is_an_error(scan):
    scan.libraries = []
    res = ms.task_scan_for_new_scenes({"scanPath": "/data/a"})
    assert res["success"] is False and scan.scans == []


def test_scan_invalidates_every_boxs_cache_after_starting(scan):
    ms.task_scan_for_new_scenes({"scanPath": "/data/a"})
    assert scan.invalidated == [b["endpoint"] for b in BOXES]
    assert scan.events[0] == "scan" and set(scan.events[1:]) == {"inv"}


def test_failed_scan_does_not_invalidate(scan):
    scan.scan_result = None
    res = ms.task_scan_for_new_scenes({"scanPath": "/data/a"})
    assert res["success"] is False and scan.invalidated == []


def test_scan_uses_the_ui_task_defaults_from_a_json_string(scan):
    scan.ui = json.dumps({"taskDefaults": {"scan": {"scanGeneratePreviews": True}}})
    ms.task_scan_for_new_scenes({"scanPath": "/data/a"})
    assert scan.scans[0]["scanGeneratePreviews"] is True


def test_no_redundant_json_import():
    assert "json_module" not in inspect.getsource(ms)


# ---- Whisparr settings are stripped -------------------------------------------------

def test_whisparr_url_and_root_folder_are_stripped(monkeypatch):
    seen = install(monkeypatch, whisparr(lookup=[{"movie": {"title": "T", "foreignId": "f1", "stashId": SID}}]))
    settings = dict(ADD_SETTINGS, whisparrUrl="  http://whisparr:6969/  ", whisparrRootFolder="  /data/videos \n")
    res = ms.add_to_whisparr(SID, "T", settings, endpoint=STASHDB)
    assert res["success"] is True
    post = next(r for r in seen if r.get_method() == "POST")
    assert json.loads(post.data)["rootFolderPath"] == "/data/videos"
    assert all(r.full_url.startswith("http://whisparr:6969/api/v3/") for r in seen)


def test_blank_root_folder_is_not_configured(monkeypatch):
    install(monkeypatch, whisparr())
    res = ms.add_to_whisparr(SID, "T", dict(ADD_SETTINGS, whisparrRootFolder="   "), endpoint=STASHDB)
    assert res["success"] is False and "root folder" in res["error"]


def test_blank_url_is_not_configured(monkeypatch):
    install(monkeypatch, whisparr())
    res = ms.add_to_whisparr(SID, "T", dict(ADD_SETTINGS, whisparrUrl="  "), endpoint=STASHDB)
    assert res["success"] is False and "not configured" in res["error"]


# ---- every setting's description matches the code default ----------------------------

# The default the code applies when the setting is unset: the `.get(key, default)` in
# missing_scenes.py or stashbox_api.DEFAULT_CONFIG. A new BOOLEAN/NUMBER setting fails
# here until it is added, so its description gets checked.
CODE_DEFAULTS = {
    "whisparrQualityProfile": 1,          # int(settings.get("whisparrQualityProfile") or 1)
    "whisparrSkipTlsVerify": False,       # plugin_settings.get("whisparrSkipTlsVerify")
    "whisparrSearchOnAdd": False,         # plugin_settings.get("whisparrSearchOnAdd", False)
    "enableAutoCleanup": False,           # plugin_settings.get("enableAutoCleanup", False)
    "unmonitorOnly": False,               # plugin_settings.get("unmonitorOnly", False)
    "stashbox_request_delay": stashbox_api.DEFAULT_CONFIG["request_delay"],
    "stashbox_max_retries": stashbox_api.DEFAULT_CONFIG["max_retries"],
    "favoriteLimit": 100,                 # int(plugin_settings.get("favoriteLimit") or 100)
    "ignoreFingerprintMatches": False,    # plugin_settings.get("ignoreFingerprintMatches")
}


def yml_settings():
    with open(os.path.join(HERE, "missingScenes.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["settings"]


def test_code_defaults_table_is_current():
    src = open(os.path.join(HERE, "missing_scenes.py"), encoding="utf-8").read()
    assert 'get("whisparrSearchOnAdd", False)' in src
    assert 'get("enableAutoCleanup", False)' in src
    assert 'get("unmonitorOnly", False)' in src
    assert 'get("favoriteLimit") or 100' in src
    assert 'get("whisparrQualityProfile") or 1' in src


def test_every_setting_description_matches_its_code_default():
    checked = []
    for key, spec in yml_settings().items():
        if spec["type"] not in ("BOOLEAN", "NUMBER"):
            continue
        assert key in CODE_DEFAULTS, f"{key}: add its code default to CODE_DEFAULTS"
        default = CODE_DEFAULTS[key]
        text = spec["description"]
        if spec["type"] == "BOOLEAN":
            said = re.search(r"\b(on|off|enabled|disabled) by default", text, re.IGNORECASE)
            if said:
                stated = said.group(1).lower() in ("on", "enabled")
                assert stated is default, f"{key}: says '{said.group(0)}' but the code default is {default}"
            else:
                assert default is False, f"{key}: no default stated, code default is {default}"
        else:
            said = re.search(r"default(?: is| of)? (\d+(?:\.\d+)?)", text, re.IGNORECASE)
            assert said, f"{key}: states no default (code: {default})"
            assert float(said.group(1)) == float(default), \
                f"{key}: says {said.group(1)} but the code default is {default}"
        checked.append(key)
    assert set(checked) == set(CODE_DEFAULTS)
