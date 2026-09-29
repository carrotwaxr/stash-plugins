# mcMetadata Plugin for [Stash](https://github.com/stashapp/stash)

**Version**: 1.6.0

This plugin is for users who manage their collection with Stash but serve content via Jellyfin, Emby, or Plex. Instead of relying on those media servers' scrapers, mcMetadata leverages your Stash database to generate `.nfo` metadata files and performer images that your media server can use.

## Features

- **NFO Generation**: Creates `.nfo` files with scene metadata (title, performers, studio, tags, date, rating)
- **File Organization**: Renames and moves video files based on customizable templates
- **Performer Images**: Exports performer images to your media server's People metadata folder
- **Dry Run Mode**: Preview all changes before committing them
- **Bulk Operations**: Process your entire library or update scenes one at a time via hooks

## Installation

### From Stash Plugin Index (Recommended)

1. Go to **Settings → Plugins → Available Plugins**
2. Add source: `https://carrotwaxr.github.io/stash-plugins/stable/index.yml`
3. Find "mcMetadata" under "Carrot Waxxer" and click Install
4. Reload plugins

### Manual Installation

1. Download or clone this repository to your Stash plugins directory
2. Reload plugins in Stash

## Configuration

All settings are configured through Stash's UI at **Settings → Plugins → mcMetadata**.

### General Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| **Dry Run Mode** | Boolean | On | Preview changes without making them. Check logs to see what would happen. |
| **Log File Path** | String | - | Also write the log to this file (for example `/data/mcMetadata.log`). Leave empty for none. See [Logging](#logging). |
| **Enable Scene Update Hook** | Boolean | Off | Automatically process scenes when you update them. |

### Processing Conditions

Conditions control **which scenes get processed**. They apply identically to both the
hook and the **Bulk Update Scenes** task. Each condition is independently optional - an
unset condition never blocks - and all configured conditions must pass (AND).

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| **Organized Condition** | String | `ignore` | `require` = only Organized scenes · `skip` = only NOT-yet-organized scenes · `ignore` = process either. Supersedes the old Hook Trigger Mode. |
| **Require StashDB Link** | Boolean | Off | Only process scenes linked to StashDB. (Applies to bulk too - when off, scenes without a StashID are processed.) |
| **Hook Trigger Mode (deprecated)** | String | - | Old name for Organized Condition. Only used when Organized Condition is empty: `on_organized` means `require`, `always` means `ignore`. |
| **Required Tags** | String | - | Comma-separated tag names; a scene is processed only if it has **at least one**. Tag names are matched without regard to case. Example: `Curated, For Jellyfin` |
| **Include Paths** | String | - | Comma-separated path globs; a scene is processed only if a file matches one. Example: `/media/curated/*` |
| **Exclude Paths** | String | - | Comma-separated path globs; a scene is skipped if a file matches one. **Exclude wins over Include.** Example: `*/trash/*` |

Path globs are case-insensitive and `*` spans directory separators, so `/media/curated/*`
also matches files in its subfolders.

**Conditions and "mark organized".** When the renamer is on, **Mark Scenes as Organized**
sets the Organized flag after a scene's files move. That interacts with Organized Condition:

- `skip` processes each scene once. After the move the scene is Organized, so later runs skip it.
- `require` only processes scenes you (or another plugin) already marked Organized.
- The hook ignores the "mark organized" update that mcMetadata makes itself, so it doesn't
  process the same scene twice. Your own later edits still trigger it.
- A dry run never marks scenes organized.

**Bad values.** If Organized Condition holds a value other than `require`, `skip` or `ignore`,
mcMetadata logs a warning and processes **no** scenes until you fix it. A wrong value can't
process more than you meant to. For other settings, a bad value falls back to its default
with a warning. Boolean settings accept `true`/`false` and `1`/`0`.

**Worked example** - only generate NFOs for your Organized, StashDB-linked scenes under
`/media/curated`:

- Organized Condition = `require`
- Require StashDB Link = `On`
- Include Paths = `/media/curated/*`

When you run **Bulk Update Scenes** in Dry Run, the log ends with a histogram of how many
scenes were skipped and why, plus a sample - so you can confirm your conditions before a
live run:

```
[DRY RUN] Bulk scan complete: 1240 scanned
  -> processed: 312
  -> skipped: 928
      not_organized........... 700
      missing_required_tag.... 180
      outside_include_paths... 48
  sample skipped: [41] not_organized | [88] missing_required_tag
```

> **Migrating from Hook Trigger Mode?** It's deprecated but still honored when Organized
> Condition is left unset: `on_organized` → `require`, `always` → `ignore`. Set Organized
> Condition to take over.

### File Renamer Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| **Enable File Renamer** | Boolean | Off | Move/rename video files based on the path template |
| **Renamer Base Path** | String | - | Base directory for renamed files. Must be inside a Stash library. |
| **Renamer Path Template** | String | `$Studio/$Title - $Performers $ReleaseDate [$Resolution]` | Template for file paths. See variables below. |
| **Max Filepath Length** | Number | 250 | Maximum total path length. Values outside 40-800 are clamped, with a warning. |
| **Skip Files Already in Path** | Boolean | Off | Don't rename files already in the renamer base path |
| **Mark Scenes as Organized** | Boolean | On | Set the Organized flag after renaming |
| **Multi-File Mode** | String | `all` | How to handle scenes with multiple files: `all`, `primary_only`, or `skip` |
| **Move Sidecar Files** | Boolean | On | Move files named like the video (subtitles, funscripts and so on) along with it. See [Sidecar files](#sidecar-files). |

### Rename Rules

- **Base path.** The Renamer Base Path must be inside one of your Stash libraries. Stash
  refuses moves outside them. mcMetadata checks this and skips the move with a clear message.
  If it can't read your library list, it warns and lets Stash decide.
- **Sanitizing.** Every folder and file name made from the template is cleaned the same way.
  `:` becomes `-`. Other characters Windows forbids (`<>"/\|?*`) and control characters
  become a space. Repeated spaces collapse, and leading or trailing spaces and dots are
  removed. Reserved Windows names such as `nul` get an underscore. `&` is kept. A value
  can't contain a slash, so a title can't add folders or climb out of the base path.
  Performer image paths get the same treatment and can't leave the People folder.
- **One name at a time.** The template is filled in a single pass. Text inside a variable's
  value is never treated as a template variable.
- **255 bytes per name.** No single folder or file name may be longer than 255 bytes
  (UTF-8, so non-Latin names count more per character). Longer names are trimmed at a
  character boundary, and the file extension is kept.
- **Path length.** Max Filepath Length (default 250) limits the whole path. Only a path that
  is too long is shortened. mcMetadata shortens `$Tags` first, then the performer variables,
  dropping whole names from the end. A last remaining name is cut short rather than dropped.
  Other parts of the template are never shortened; if the path still doesn't fit, the scene
  is skipped with an error.
- **Collisions.** When two files of the same scene would get the same name, the later ones
  get a numbered suffix such as ` (2)`, kept within the length limit. When the name is taken
  by a file already on disk, that file is left alone and the move is skipped with a warning.
  mcMetadata never overwrites an existing file.
- **Templates that may collide.** A template without `$StashID`, or without a studio plus
  `$Title` plus `$ReleaseDate`, logs a warning. Renames still run. An **empty** template turns
  renaming off for that run.
- **Case-only renames** (`clip.mp4` to `Clip.mp4`) work on case-insensitive file systems.
- **Bulk runs** page through scenes by id, so none are skipped when files move mid-run.

**What dry run checks.** A dry run builds the same paths a live run would, and reports the
same library check, existing-file check and collision suffixes. It also accounts for the moves
it skipped earlier in the same run. It can't see problems that only show up when Stash moves
the file: permissions, a full disk, or files that change while the run is going.

### NFO Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| **Skip Existing NFO Files** | Boolean | Off | Don't overwrite NFO files that already exist |
| **NFO Exclude Fields** | String | - | Comma-separated fields to leave out. See [Excludable NFO fields](#excludable-nfo-fields). |
| **NFO File Name** | String | `{basename}.nfo` | Name of the NFO file. `{basename}` is the video's file name without its extension. |
| **Poster File Name** | String | `{basename}-poster.jpg` | Name of the poster image, downloaded from the scene screenshot. |
| **Backdrop File Name** | String | - | Optional backdrop image, also from the screenshot. Empty means none (Plex mode uses `{basename}-fanart.jpg`). |
| **NFO Rating Field** | String | `both` | Which rating tag to write: `both` (`rating` and `userrating`), `rating`, or `userrating`. |

Every NFO is valid XML. Control characters are removed, `]]>` in a description is handled,
and empty elements are left out. An NFO includes a `<director>` when the scene has one.
Each scene also gets `<uniqueid>` entries: the Stash id (marked `default="true"`) and one for
each stash-box link (`stashdb`, `theporndb`, `fansdb`, or the site name). A ThePornDB link
also writes `<theporndbid>`.

```xml
<uniqueid type="stash" default="true">123</uniqueid>
<uniqueid type="stashdb">00000000-0000-0000-0000-000000000000</uniqueid>
```

#### Excludable NFO fields

Put these names in **NFO Exclude Fields**, separated by commas. Unknown names are ignored
with a warning.

`name`, `title`, `originaltitle`, `sorttitle`, `criticrating`, `rating`, `userrating`, `plot`,
`premiered`, `releasedate`, `year`, `studio`, `uniqueid`, `genre`, `thumb` (or `poster`, the poster
image reference), `actor` (all performers), `tag` (all tags).

**File names.** A name without `{basename}` (such as `movie.nfo`, `poster.jpg` or `folder.jpg`)
is shared by every video in the folder, so it is only written when the folder holds a single
video. Names must end in `.nfo` (NFO) or an image extension (images), and can't contain a slash.

### Sidecar files

With **Move Sidecar Files** on (the default), files named like the video move with it and are
renamed to match. A file is a sidecar if its name is the video's name followed by `.` or `-`,
for example `Clip.srt`, `Clip.en.srt`, `Clip.funscript` or `Clip-poster.jpg`.

- A file belongs to the video with the **longest** matching name, so `Clip 2.srt` goes with
  `Clip 2.mp4`, not `Clip.mp4`.
- Other videos are never treated as sidecars.
- Nothing is overwritten. If a file with the same name already exists at the destination, it stays and the sidecar stays too.
- Folder-level files such as `folder.jpg` or `movie.nfo` aren't moved into a destination
  folder that already holds other videos.

With the setting off, only the NFO and poster that mcMetadata itself writes move with the video.

### Actor Image Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| **Enable Actor Images** | Boolean | Off | Copy performer images to the media server's metadata folder. Also re-exports a performer's image when you update that performer in Stash. |
| **Media Server Type** | String | `jellyfin` | Target media server: `jellyfin`, `emby`, or `plex` |
| **Actor Metadata Path** | String | - | Path to media server's People metadata folder |

## Template Variables

Use these variables in your **Renamer Path Template**:

| Variable | Description |
|----------|-------------|
| `$Studio` | Scene's studio name |
| `$Studios` | Full studio hierarchy as nested directories |
| `$Title` | Scene title |
| `$StashID` | Scene's Stash ID |
| `$ReleaseDate` | Scene's release date (YYYY-MM-DD) |
| `$ReleaseYear` | Year from release date |
| `$Resolution` | Video resolution (480p, 720p, 1080p, 1440p, 4K, 8K) |
| `$Quality` | Video quality (LOW, SD, HD, FHD, 2K, QHD, UHD, FUHD) |
| `$Performers` | All performer names (space-separated) |
| `$FemalePerformers` | Female performer names only |
| `$MalePerformers` | Male performer names only |
| `$Tags` | All scene tags (space-separated) |

### Conditional Blocks

Wrap parts of your template in `{curly braces}` to include them only when a variable has a value:

| Template | With Date | Without Date |
|----------|-----------|--------------|
| `{$ReleaseDate - }$Title` | `2024-01-15 - My Scene` | `My Scene` |
| `$Studio/{$ReleaseYear/}$Title` | `Studio/2024/My Scene` | `Studio/My Scene` |

If a block contains multiple variables, ALL must have values for the block to appear.

**Uniqueness**: so that no two scenes get the same path, a template should contain either:
- `$StashID`, OR
- (`$Studio` or `$Studios`) AND `$Title` AND `$ReleaseDate`

Other templates log a warning but still rename. A scene whose path is already taken is not moved.

## Usage

### Running Bulk Tasks

1. Go to **Settings → Tasks → Plugin Tasks**
2. Select:
   - **Bulk Update Scenes**: Process all scenes (subject to your Processing Conditions)
   - **Bulk Update Performers**: Copy all performer images to media server

### Using the Hook

1. Enable **"Enable Scene Update Hook"** in plugin settings
2. When you update a scene in Stash, the plugin will automatically:
   - Rename/move the video file (if renamer enabled)
   - Generate/update the NFO file
   - Copy performer images (if enabled)
3. When you update a performer, the plugin re-exports that performer's image (only if **Enable Actor Images** is on).

If the hook is off, it does nothing and writes nothing to the log.

### Recommended Workflow

1. **First Run**: Enable Dry Run Mode, configure your settings, run "Bulk Update Scenes"
2. **Check Logs**: Go to Settings → Logs (Debug level) to see what would happen
3. **Execute**: Disable Dry Run Mode, run "Bulk Update Scenes" again
4. **Ongoing**: Enable the hook for automatic updates on scene changes

## Media Server Setup

### Jellyfin

- **Actor Metadata Path**: `<jellyfin-config>/data/metadata/People/`
- **Folder Structure**: `People/J/Jane Doe/folder.jpg` (uses A-Z subfolders)

Recipe for a folder-per-movie library (`Movies/<Studio>/<Title>/<Title>.mp4`), where Jellyfin
looks for `movie.nfo`, `poster.jpg` and `backdrop.jpg`:

| Setting | Value |
|---------|-------|
| Renamer Path Template | `$Studio/$Title{ ($ReleaseDate)}/$Title` |
| NFO File Name | `movie.nfo` |
| Poster File Name | `poster.jpg` |
| Backdrop File Name | `backdrop.jpg` |
| NFO Rating Field | `both` (or `rating` if Jellyfin should only show the community rating) |
| Enable Actor Images | On |

Each of those file names has no `{basename}`, so it is written only when the folder holds one
video. If you keep several videos in one folder, leave the defaults (`{basename}.nfo` and
`{basename}-poster.jpg`) and set the backdrop to `{basename}-fanart.jpg`. Jellyfin also reads
the `<uniqueid>` and `<director>` entries. Updating a performer in Stash re-exports that
performer's image.

### Emby

- **Actor Metadata Path**: `<emby-config>/metadata/People/`
- **Folder Structure**: `People/Jane Doe/folder.jpg` (no subfolders)

### Plex

Plex Media Server 1.43.1 and later reads Kodi-style NFO files with its built-in local NFO support, so set **Media Server Type** to `plex` and no third-party agent is needed.

> **Not yet checked against a live Plex.** Plex's support pages weren't reachable while this was written, so Plex mode follows the documented format but hasn't been tested on a running Plex Media Server. Please report anything that doesn't import.

Library setup:

1. Create (or edit) a **Movies** library and, under **Advanced**, choose **Plex NFO Movie** as the metadata agent. The Plex NFO provider must be enabled for that library; enable **Use local assets** so the poster and fanart files are read.
2. Run mcMetadata on your scenes. Each scene gets `<video>.nfo`, `<video>-poster.jpg` and, in Plex mode only, `<video>-fanart.jpg` (the scene screenshot) next to the video.
3. Scan the library (or **Refresh Metadata**) in Plex.

Plex mode notes:

- **Fanart**: the backdrop is written as `<video>-fanart.jpg`. Set **Backdrop File Name** to use another name instead. Only one backdrop file is written.
- **Actors**: `<actor>` entries (name, role, order) are written for every scene.
- **Performer images**: Plex does not read local People folders, and it loads an actor `<thumb>` only from a URL, without credentials. In Plex mode the NFO therefore uses the Stash performer image URL (`/performer/<id>/image`) as the actor thumb, and only when "Enable Actor Images" is on and your Stash has no API key configured. If Stash requires authentication, the thumb is omitted (no API key is ever written to an NFO) and Plex shows the actors without pictures.
- **Legacy agent**: on older Plex versions, the third-party [XBMCnfoMoviesImporter](https://github.com/gboudreau/XBMCnfoMoviesImporter.bundle) agent can import the same NFO files.

## Logging

Set **Log File Path** to also write the log to a file. New runs append to it, each run starts
with one header line, and the file is rotated to `<name>.1` when it grows past 5 MB. Without a
path, logs only go to **Settings → Logs**.

## Image downloads

Posters and backdrops are downloaded from your Stash. mcMetadata signs in with the Stash
session cookie, or the API key if there is one. Performers that only have Stash's default
image are skipped. Redirects are refused, because a redirect usually means Stash sent the
login page, and the plugin would otherwise save that page as an image. API keys are masked in
logs.

## Troubleshooting

Enable debug logging at **Settings → Logs** and set Log Level to Debug. The plugin logs detailed information prefixed with `[DRY RUN]` when in dry run mode.

Common issues:
- **Files not moving**: Check that the Renamer Base Path is within a Stash library, and read the log for the reason (outside every library, destination already exists, path too long)
- **No scenes processed**: Check that Organized Condition is exactly `require`, `skip` or `ignore`. Any other value processes nothing
- **Image download fails**: Make sure Stash is reachable from the plugin at its configured address and, if you use authentication, that you're signed in or have an API key
- **NFO not parsing**: Ensure your media server is set to read local NFO files
- **Performer images not showing**: Verify the actor metadata path is correct for your media server

## Requirements

- Stash v0.24.0 or later
- Python 3.9+ (included in the official Stash Docker image)
- `stashapp-tools>=0.2.59`. Stash doesn't install it for you; see [Python prerequisites](../../README.md#python-prerequisites). If it's missing or too old, the plugin stops with a message saying so.

## Development

```bash
cd plugins/mcMetadata
pip install -r requirements.txt pytest
python -m pytest
```

Tests that talk to a real Stash, StashDB or Whisparr skip unless `STASH_PLUGINS_INTEGRATION=1` is set. Point them at a test instance, never production. For example:

```bash
STASH_PLUGINS_INTEGRATION=1 STASH_URL=http://localhost:9999 STASH_API_KEY=... python -m pytest tests/test_integration.py
```

`tests/manual_run.py` runs the plugin against one scene by hand; see its docstring.

## Changelog

### v1.6.0
- **Safer renames**: names from the template are sanitized in one place, capped at 255 bytes each, and can't escape the base path. The base path must be inside a Stash library. Max Filepath Length is clamped to 40-800 and only shortens a path that is too long (tags first, then whole performer names). Collision suffixes such as ` (2)` respect the limit, and a template that may collide now warns instead of stopping renames. Case-only renames work on case-insensitive file systems.
- **Sidecar files** (subtitles, funscripts and so on) move with the video. New setting: Move Sidecar Files (on by default).
- **Bulk runs** no longer skip scenes. The hook ignores mcMetadata's own "mark organized" update.
- **NFO**: always valid XML. `thumb`, `poster`, `actor` and `tag` can now be excluded. New settings: NFO File Name, Poster File Name, Backdrop File Name, NFO Rating Field. NFOs include `<director>` and `<uniqueid>` entries for the Stash id and each stash-box link (plus `<theporndbid>`).
- **Plex mode** for Plex Media Server 1.43.1+: fanart file and actor image URLs. Not yet checked against a live Plex.
- **Jellyfin**: folder-per-movie file names. A Performer.Update.Post hook re-exports a performer's image.
- **Settings**: bad values fall back to defaults with a warning, booleans accept `true`/`false` and `1`/`0`, and an invalid Organized Condition processes no scenes. A disabled hook logs nothing.
- **Logging**: the log file appends, has one header per run, and rotates at 5 MB. Path globs and tag names are matched without regard to case.
- **Downloads**: authenticated with the session cookie or API key, default performer images are skipped, redirects are refused and API keys are masked in logs. Saved images are readable by other users, such as a media server (they were saved as owner-only).
- Dry runs now match live runs. A clear error tells you when `stashapp-tools` is missing.

### v1.5.0
- **Unified Processing Conditions** applied to both the hook and the bulk task: Organized Condition (`require`/`skip`/`ignore`), Required Tags, and Include/Exclude path globs, alongside the existing Require StashDB Link
- **Fixed #127**: the bulk task no longer silently skips scenes without a StashID - it now processes all scenes (subject to your conditions)
- Bulk Dry Run now prints a skip-reason histogram + sample, so you can preview what would be processed before a live run
- `hookTriggerMode` is deprecated in favor of Organized Condition (auto-migrated when Organized Condition is unset: `on_organized` → require, `always` → ignore)

### v1.4.0
- Added `hookTriggerMode` setting: choose to process scenes on every save (`always`) or only when marked Organized (`on_organized`) (#111)
- Added conditional template blocks: `{$ReleaseDate - }$Title` includes text only when the variable has a value (#112)
- Added `nfoExcludeFields` setting to omit specific fields from NFO files (#113)

### v1.3.0
- Added Plex as a supported media server (poster files work natively, NFO requires third-party agent, performer images not supported)
- Fixed image download validation to prevent corrupt/truncated files (Content-Length check, minimum size, JPEG EOI/PNG IEND markers, retry logic)
- Added NFO artwork references: `<thumb aspect="poster">` for scene poster and `<thumb>` tags in `<actor>` blocks for performer images

### v1.2.2
- Added "Require StashDB Link" setting for hook processing (Issue #14)
- NFO files now generated for locally edited scenes by default (not just StashDB-linked scenes)
- Users who want curated-only content can enable the new setting

### v1.2.1
- Added explicit defaults to all settings in plugin YAML
- Dry Run Mode now correctly defaults to ON for new installations

### v1.2.0
- Fixed Emby actor folder structure (no A-Z subfolders)
- Fixed XML escaping for special characters in titles (ampersands, etc.)
- Added comprehensive unit tests

### v1.1.0
- Migrated settings from `settings.ini` to Stash's native plugin settings UI
- Added "Enable Scene Update Hook" setting
- Removed toggle tasks (now controlled via settings UI)

### v1.0.0
- Replaced direct SQLite manipulation with GraphQL `moveFiles` mutation
- Added multi-file scene handling (all/primary_only/skip modes)
- Added `nfo_skip_existing` setting
- Fixed pagination bug in bulk operations
- Replaced Python 3.10+ syntax for broader compatibility
- Improved error handling and progress reporting

## License

MIT License - See [LICENSE](LICENSE) file
