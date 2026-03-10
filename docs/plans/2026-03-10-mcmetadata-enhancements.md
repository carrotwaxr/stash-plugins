# mcMetadata Enhancements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add three user-requested enhancements to mcMetadata: configurable hook triggers (#111), conditional rename template syntax (#112), and configurable NFO field exclusion (#113).

**Architecture:** All three features add new settings to `mcMetadata.yml` and `mcMetadata.py:get_settings()`, then modify the relevant module. Hook trigger mode adds a gate in `mcMetadata.py`'s hook handler. Conditional templates add a pre-processing pass in `utils/replacer.py`. NFO exclusion adds field filtering in `utils/nfo.py`.

**Tech Stack:** Python 3.9+, pytest with unittest.TestCase, stashapi (mocked in tests)

---

## Task 1: Hook Trigger Mode Setting (#111)

**Files:**
- Modify: `plugins/mcMetadata/mcMetadata.yml:23-28`
- Modify: `plugins/mcMetadata/mcMetadata.py:83-104`
- Modify: `plugins/mcMetadata/mcMetadata.py:167-194`
- Test: `plugins/mcMetadata/tests/test_unit.py`

### Step 1: Write failing tests for hook trigger mode

Add to `tests/test_unit.py`:

```python
class TestHookTriggerMode(unittest.TestCase):
    """Test hookTriggerMode setting behavior (#111)."""

    def test_always_mode_processes_unorganized_scene(self):
        """'always' mode should process scenes regardless of organized status."""
        settings = {"hook_trigger_mode": "always"}
        scene = {"id": "1", "organized": False}
        should_skip = settings.get("hook_trigger_mode", "always") == "on_organized" and not scene.get("organized", False)
        self.assertFalse(should_skip)

    def test_always_mode_processes_organized_scene(self):
        """'always' mode should process organized scenes too."""
        settings = {"hook_trigger_mode": "always"}
        scene = {"id": "1", "organized": True}
        should_skip = settings.get("hook_trigger_mode", "always") == "on_organized" and not scene.get("organized", False)
        self.assertFalse(should_skip)

    def test_on_organized_skips_unorganized_scene(self):
        """'on_organized' mode should skip unorganized scenes."""
        settings = {"hook_trigger_mode": "on_organized"}
        scene = {"id": "1", "organized": False}
        should_skip = settings.get("hook_trigger_mode", "always") == "on_organized" and not scene.get("organized", False)
        self.assertTrue(should_skip)

    def test_on_organized_processes_organized_scene(self):
        """'on_organized' mode should process organized scenes."""
        settings = {"hook_trigger_mode": "on_organized"}
        scene = {"id": "1", "organized": True}
        should_skip = settings.get("hook_trigger_mode", "always") == "on_organized" and not scene.get("organized", False)
        self.assertFalse(should_skip)

    def test_default_mode_is_always(self):
        """Missing hookTriggerMode should default to 'always'."""
        settings = {}
        mode = settings.get("hook_trigger_mode", "always")
        self.assertEqual(mode, "always")
```

### Step 2: Run tests to verify they pass (logic-only tests)

Run: `cd plugins/mcMetadata && python -m pytest tests/test_unit.py::TestHookTriggerMode -v`
Expected: PASS (these test the logic we'll embed in mcMetadata.py)

### Step 3: Add setting to mcMetadata.yml

Add after `requireStashId` setting (after line 28):

```yaml
  hookTriggerMode:
    displayName: Hook Trigger Mode
    description: "When to process scenes via hook: 'always' (every save) or 'on_organized' (only when scene is marked Organized). Default is 'always'."
    type: STRING
```

### Step 4: Add setting to get_settings() in mcMetadata.py

Add to the return dict in `get_settings()` (after line 89):

```python
        "hook_trigger_mode": plugin_config.get("hookTriggerMode", "always"),
```

### Step 5: Add trigger mode check to hook handler in mcMetadata.py

In the `Scene.Update.Post` handler block (around line 188), add BEFORE the existing cascade protection check:

```python
            # Check hook trigger mode
            hook_trigger_mode = SETTINGS.get("hook_trigger_mode", "always")
            if hook_trigger_mode == "on_organized" and not scene.get("organized", False):
                log.debug(f"Scene {scene_id} not organized, skipping (hookTriggerMode=on_organized)")
                return
```

The existing cascade protection (lines 189-191) stays as-is — it prevents re-firing after we mark organized during rename.

### Step 6: Run all tests

Run: `cd plugins/mcMetadata && python -m pytest tests/test_unit.py -v`
Expected: All PASS

### Step 7: Commit

```bash
git add plugins/mcMetadata/mcMetadata.yml plugins/mcMetadata/mcMetadata.py plugins/mcMetadata/tests/test_unit.py
git commit -m "feat(mcMetadata): add hookTriggerMode setting (#111)

Allow users to configure when the hook fires: 'always' (default,
backward-compatible) or 'on_organized' (only when scene is marked
Organized). Useful for users who make incremental edits."
```

---

## Task 2: Conditional Template Blocks (#112)

**Files:**
- Modify: `plugins/mcMetadata/utils/replacer.py`
- Test: `plugins/mcMetadata/tests/test_unit.py`

### Step 1: Write failing tests for conditional blocks

Add to `tests/test_unit.py`:

```python
from utils.replacer import get_new_path, resolve_conditionals


class TestConditionalTemplates(unittest.TestCase):
    """Test conditional template block syntax (#112)."""

    def setUp(self):
        """Scene with all fields populated."""
        self.full_scene = {
            "id": "1",
            "title": "Test Title",
            "date": "2024-01-15",
            "studio": {"name": "TestStudio", "parent_studio": None},
            "stash_ids": [{"stash_id": "abc123", "endpoint": "https://stashdb.org"}],
            "performers": [],
            "tags": [],
            "files": [{"path": "/video.mp4", "height": 1080, "width": 1920}],
        }
        self.no_date_scene = {**self.full_scene, "date": None}

    def test_conditional_included_when_var_has_value(self):
        """Block should be included when variable resolves."""
        result = resolve_conditionals("{$ReleaseDate - }$Title", self.full_scene)
        self.assertEqual(result, "2024-01-15 - $Title")

    def test_conditional_removed_when_var_empty(self):
        """Block should be removed when variable has no value."""
        result = resolve_conditionals("{$ReleaseDate - }$Title", self.no_date_scene)
        self.assertEqual(result, "$Title")

    def test_multiple_conditionals(self):
        """Multiple conditional blocks should each resolve independently."""
        result = resolve_conditionals("{$ReleaseDate - }{$Studio/}$Title", self.full_scene)
        self.assertEqual(result, "2024-01-15 - TestStudio/$Title")

    def test_conditional_with_no_vars_passes_through(self):
        """Braces with no variables inside are literal text."""
        result = resolve_conditionals("{novar}$Title", self.full_scene)
        self.assertEqual(result, "{novar}$Title")

    def test_conditional_var_at_start_of_block(self):
        """`{$ReleaseDate}` with no surrounding text should work."""
        result = resolve_conditionals("{$ReleaseDate}$Title", self.full_scene)
        self.assertEqual(result, "2024-01-15$Title")

    def test_conditional_var_at_end_of_block(self):
        """`{- $ReleaseDate}` should work."""
        result = resolve_conditionals("{- $ReleaseDate}$Title", self.full_scene)
        self.assertEqual(result, "- 2024-01-15$Title")

    def test_conditional_multiple_vars_all_present(self):
        """Block with multiple vars should be included when all resolve."""
        result = resolve_conditionals("{$Studio $ReleaseDate - }$Title", self.full_scene)
        self.assertEqual(result, "TestStudio 2024-01-15 - $Title")

    def test_conditional_multiple_vars_one_missing(self):
        """Block with multiple vars should be removed if any var missing."""
        result = resolve_conditionals("{$Studio $ReleaseDate - }$Title", self.no_date_scene)
        self.assertEqual(result, "$Title")

    def test_end_to_end_with_conditional(self):
        """Full get_new_path with conditional template should work."""
        template = "{$ReleaseDate - }$Title"
        path = get_new_path(self.full_scene, "/base/", template, 250)
        self.assertEqual(path, "/base/2024-01-15 - Test Title.mp4")

    def test_end_to_end_conditional_empty(self):
        """Full get_new_path with empty conditional should produce clean output."""
        template = "{$ReleaseDate - }$Title"
        path = get_new_path(self.no_date_scene, "/base/", template, 250)
        self.assertEqual(path, "/base/Test Title.mp4")
```

### Step 2: Run tests to verify they fail

Run: `cd plugins/mcMetadata && python -m pytest tests/test_unit.py::TestConditionalTemplates -v`
Expected: FAIL — `resolve_conditionals` not defined

### Step 3: Implement resolve_conditionals in replacer.py

Add this function before `get_new_path()`:

```python
def resolve_conditionals(template, scene):
    """Resolve conditional blocks in a template string.

    Syntax: {literal$Variableliteral} — the entire block (including literal
    text) is included only if ALL $Variables inside resolve to non-empty values.
    If any variable raises ValueError or resolves empty, the whole block is removed.

    Blocks without any $Variables are left as-is (treated as literal braces).

    Args:
        template: Template string potentially containing {conditional} blocks
        scene: Scene dict for variable resolution

    Returns:
        str: Template with conditional blocks resolved
    """
    def _resolve_block(match):
        block_content = match.group(1)

        # Find all $Variables in this block
        var_matches = re.findall(r'\$[A-Za-z]+', block_content)
        if not var_matches:
            # No variables — treat braces as literal
            return match.group(0)

        # Check each variable resolves to a non-empty value
        resolved = block_content
        for var in var_matches:
            if var not in replacers:
                return ""  # Unknown variable — remove block
            try:
                value = replacers[var](scene)
                if not value:
                    return ""
                resolved = re.sub(__get_replacer_regex(var), value, resolved)
            except (ValueError, KeyError):
                return ""  # Variable can't resolve — remove block

        return resolved

    return re.sub(r'\{([^}]*\$[A-Za-z][^}]*)\}', _resolve_block, template)
```

### Step 4: Integrate into get_new_path()

In `get_new_path()`, add conditional resolution as the first step after the budget check (around line 195, before the replacer loop):

```python
        # Resolve conditional blocks before standard replacement
        template = resolve_conditionals(template, scene)
```

### Step 5: Run tests to verify they pass

Run: `cd plugins/mcMetadata && python -m pytest tests/test_unit.py::TestConditionalTemplates -v`
Expected: All PASS

### Step 6: Run full test suite

Run: `cd plugins/mcMetadata && python -m pytest tests/test_unit.py -v`
Expected: All PASS

### Step 7: Commit

```bash
git add plugins/mcMetadata/utils/replacer.py plugins/mcMetadata/tests/test_unit.py
git commit -m "feat(mcMetadata): add conditional template blocks (#112)

Support {$Variable literal} syntax in rename templates where the entire
block is only included if the variable has a value. Prevents ugly
dangling separators when optional fields are empty.

Example: {$ReleaseDate - }$Title produces '2024-01-15 - Title' when
date exists, or just 'Title' when it doesn't."
```

---

## Task 3: NFO Exclude Fields (#113)

**Files:**
- Modify: `plugins/mcMetadata/mcMetadata.yml`
- Modify: `plugins/mcMetadata/mcMetadata.py:83-104`
- Modify: `plugins/mcMetadata/utils/nfo.py`
- Test: `plugins/mcMetadata/tests/test_unit.py`

### Step 1: Write failing tests for NFO field exclusion

Add to `tests/test_unit.py`:

```python
class TestNfoExcludeFields(unittest.TestCase):
    """Test NFO field exclusion (#113)."""

    def setUp(self):
        self.mock_scene = {
            "id": "123",
            "title": "Test Scene",
            "details": "Description",
            "date": "2024-01-15",
            "rating100": 80,
            "studio": {"name": "Test Studio"},
            "performers": [{"name": "Jane Doe"}],
            "tags": [{"name": "Tag1"}],
            "files": [{"path": "/path/to/video.mp4"}],
        }

    def test_no_exclusions_produces_all_fields(self):
        """Empty exclude list should produce all fields (backward compatible)."""
        settings = {"nfo_exclude_fields": []}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertIn("<criticrating>", nfo)
        self.assertIn("<uniqueid", nfo)
        self.assertIn("<rating>", nfo)
        self.assertIn("<userrating>", nfo)

    def test_exclude_uniqueid(self):
        """Should omit uniqueid when excluded."""
        settings = {"nfo_exclude_fields": ["uniqueid"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertNotIn("<uniqueid", nfo)
        self.assertIn("<title>", nfo)

    def test_exclude_rating_fields(self):
        """Should omit all rating fields when excluded."""
        settings = {"nfo_exclude_fields": ["criticrating", "rating", "userrating"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertNotIn("<criticrating>", nfo)
        self.assertNotIn("<rating>", nfo)
        self.assertNotIn("<userrating>", nfo)
        self.assertIn("<title>", nfo)

    def test_exclude_multiple_fields(self):
        """Should handle excluding multiple unrelated fields."""
        settings = {"nfo_exclude_fields": ["sorttitle", "originaltitle", "year"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertNotIn("<sorttitle>", nfo)
        self.assertNotIn("<originaltitle>", nfo)
        self.assertNotIn("<year>", nfo)
        self.assertIn("<title>", nfo)
        self.assertIn("<premiered>", nfo)

    def test_exclude_does_not_affect_performers(self):
        """Performers should always be included regardless of exclusions."""
        settings = {"nfo_exclude_fields": ["uniqueid", "criticrating"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertIn("<actor>", nfo)
        self.assertIn("<name>Jane Doe</name>", nfo)

    def test_exclude_does_not_affect_tags(self):
        """Tags should always be included regardless of exclusions."""
        settings = {"nfo_exclude_fields": ["uniqueid"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertIn("<tag>Tag1</tag>", nfo)

    def test_exclude_genre(self):
        """Should omit genre when excluded."""
        settings = {"nfo_exclude_fields": ["genre"]}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertNotIn("<genre>", nfo)

    def test_no_settings_produces_all_fields(self):
        """No settings at all should produce all fields (backward compatible)."""
        nfo = build_nfo_xml(self.mock_scene)
        self.assertIn("<criticrating>", nfo)
        self.assertIn("<uniqueid", nfo)

    def test_none_exclude_list_produces_all_fields(self):
        """None exclude list treated as empty."""
        settings = {"nfo_exclude_fields": None}
        nfo = build_nfo_xml(self.mock_scene, settings=settings)
        self.assertIn("<criticrating>", nfo)
```

### Step 2: Run tests to verify they fail

Run: `cd plugins/mcMetadata && python -m pytest tests/test_unit.py::TestNfoExcludeFields -v`
Expected: FAIL — existing build_nfo_xml doesn't check exclude_fields

### Step 3: Add setting to mcMetadata.yml

Add after `nfoSkipExisting` setting:

```yaml
  nfoExcludeFields:
    displayName: NFO Exclude Fields
    description: "Comma-separated list of NFO fields to omit. Available: name, title, originaltitle, sorttitle, criticrating, rating, userrating, plot, premiered, releasedate, year, studio, uniqueid, genre. Leave empty to include all fields."
    type: STRING
```

### Step 4: Add setting to get_settings() in mcMetadata.py

Add to the return dict:

```python
        "nfo_exclude_fields": [
            f.strip().lower()
            for f in plugin_config.get("nfoExcludeFields", "").split(",")
            if f.strip()
        ],
```

This parses `"uniqueid, criticrating"` into `["uniqueid", "criticrating"]`.

### Step 5: Refactor build_nfo_xml to support field exclusion

Replace the entire `build_nfo_xml` function in `utils/nfo.py` with a builder approach:

```python
def build_nfo_xml(scene, settings=None, video_path=None):
    """Build NFO XML for a scene.

    Args:
        scene: Scene dict from Stash API
        settings: Plugin settings dict (optional, enables artwork references and field exclusion)
        video_path: Path to the video file (optional, enables poster thumb tag)

    Returns:
        str: NFO XML content
    """
    exclude = set()
    if settings:
        exclude = set(settings.get("nfo_exclude_fields") or [])

    id = scene["id"]
    details = scene["details"] or ""

    title = ""
    if scene["title"] is not None and scene["title"] != "":
        title = escape_xml(scene["title"])
    else:
        title = escape_xml(os.path.basename(os.path.normpath(scene["files"][0]["path"])))

    custom_rating = ""
    rating = ""
    if scene["rating100"] is not None:
        rating = round(int(scene["rating100"]) / 10)
        custom_rating = scene["rating100"]

    date = ""
    year = ""
    if scene["date"] is not None:
        date = scene["date"]
        year = scene["date"].split("-")[0]

    studio = ""
    if scene["studio"] is not None:
        studio = escape_xml(scene["studio"]["name"])

    # Build XML lines, filtering excluded fields
    lines = ['<?xml version="1.0" encoding="utf-8" standalone="yes"?>', '<movie>']

    field_lines = {
        "name": f"    <name>{title}</name>",
        "title": f"    <title>{title}</title>",
        "originaltitle": f"    <originaltitle>{title}</originaltitle>",
        "sorttitle": f"    <sorttitle>{title}</sorttitle>",
        "criticrating": f"    <criticrating>{custom_rating}</criticrating>",
        "rating": f"    <rating>{rating}</rating>",
        "userrating": f"    <userrating>{rating}</userrating>",
        "plot": f"    <plot><![CDATA[{details}]]></plot>",
        "premiered": f"    <premiered>{date}</premiered>",
        "releasedate": f"    <releasedate>{date}</releasedate>",
        "year": f"    <year>{year}</year>",
        "studio": f"    <studio>{studio}</studio>",
    }

    for field_name, line in field_lines.items():
        if field_name not in exclude:
            lines.append(line)

    # Poster thumb (always included if video_path provided)
    if video_path:
        base = os.path.splitext(os.path.basename(video_path))[0]
        poster_filename = f"{base}-poster.jpg"
        lines.append(f'    <thumb aspect="poster">{escape_xml(poster_filename)}</thumb>')

    # Performers (always included)
    for i, p in enumerate(scene["performers"]):
        performer_name = escape_xml(p["name"])
        actor_thumb = ""
        actor_image_path = _get_actor_thumb_path(p["name"], settings)
        if actor_image_path:
            actor_thumb = f"\n        <thumb>{escape_xml(actor_image_path)}</thumb>"

        lines.append(f"""    <actor>
        <name>{performer_name}</name>
        <role>{performer_name}</role>
        <order>{i}</order>
        <type>Actor</type>{actor_thumb}
    </actor>""")

    # Genre (excludable)
    if "genre" not in exclude:
        lines.append("    <genre>Adult</genre>")

    # Tags (always included)
    for t in scene["tags"]:
        lines.append(f"    <tag>{escape_xml(t['name'])}</tag>")

    # Unique ID (excludable)
    if "uniqueid" not in exclude:
        lines.append(f'    <uniqueid type="stash">{id}</uniqueid>')

    lines.append("</movie>")

    return "\n".join(lines)
```

### Step 6: Run NFO exclude tests

Run: `cd plugins/mcMetadata && python -m pytest tests/test_unit.py::TestNfoExcludeFields -v`
Expected: All PASS

### Step 7: Run full test suite to check backward compatibility

Run: `cd plugins/mcMetadata && python -m pytest tests/test_unit.py -v`
Expected: All PASS. The refactored build_nfo_xml must produce equivalent XML for existing tests. If any `TestBuildNfoXml` or `TestNfoArtworkReferences` tests fail, fix whitespace/formatting differences.

### Step 8: Commit

```bash
git add plugins/mcMetadata/mcMetadata.yml plugins/mcMetadata/mcMetadata.py plugins/mcMetadata/utils/nfo.py plugins/mcMetadata/tests/test_unit.py
git commit -m "feat(mcMetadata): add configurable NFO field exclusion (#113)

Add nfoExcludeFields setting — comma-separated list of NFO fields to
omit. Refactors NFO builder to a line-based approach that filters
excluded fields while keeping performers, tags, and poster thumb
always included."
```

---

## Task 4: Documentation & Version Bump

**Files:**
- Modify: `plugins/mcMetadata/README.md`
- Modify: `plugins/mcMetadata/mcMetadata.yml:4` (version)
- Modify: `plugins/mcMetadata/mcMetadata.py:8` (version comment)

### Step 1: Update README.md

**General Settings table** — add after "Require StashDB Link" row:

```markdown
| **Hook Trigger Mode** | String | `always` | When to process scenes via hook: `always` (every save) or `on_organized` (only when scene is marked Organized). Useful if you make incremental edits and want to trigger metadata generation only when you're done. |
```

**Template Variables section** — add after the variables table:

```markdown
### Conditional Blocks

Wrap parts of your template in `{curly braces}` to include them only when a variable has a value:

| Template | With Date | Without Date |
|----------|-----------|--------------|
| `{$ReleaseDate - }$Title` | `2024-01-15 - My Scene` | `My Scene` |
| `$Studio/{$ReleaseYear/}$Title` | `Studio/2024/My Scene` | `Studio/My Scene` |

If a block contains multiple variables, ALL must have values for the block to appear.
```

**NFO Settings table** — add after "Skip Existing NFO Files" row:

```markdown
| **NFO Exclude Fields** | String | - | Comma-separated list of fields to omit from NFO files. Available: `name`, `title`, `originaltitle`, `sorttitle`, `criticrating`, `rating`, `userrating`, `plot`, `premiered`, `releasedate`, `year`, `studio`, `uniqueid`, `genre` |
```

**Changelog** — add new version section at top:

```markdown
### v1.4.0
- Added `hookTriggerMode` setting: choose to process scenes on every save (`always`) or only when marked Organized (`on_organized`) (#111)
- Added conditional template blocks: `{$ReleaseDate - }$Title` includes text only when the variable has a value (#112)
- Added `nfoExcludeFields` setting to omit specific fields from NFO files (#113)
```

### Step 2: Bump version

In `mcMetadata.yml` line 4, change `version: 1.3.0` to `version: 1.4.0`.
In `mcMetadata.py` line 8, change `Version: 1.2.3` to `Version: 1.4.0`.

### Step 3: Run full test suite one final time

Run: `cd plugins/mcMetadata && python -m pytest tests/test_unit.py -v`
Expected: All PASS

### Step 4: Commit

```bash
git add plugins/mcMetadata/README.md plugins/mcMetadata/mcMetadata.yml plugins/mcMetadata/mcMetadata.py
git commit -m "docs(mcMetadata): update docs and bump to v1.4.0

Document hook trigger mode, conditional template blocks, and NFO
field exclusion. Update changelog."
```
