#!/usr/bin/env python3
"""
Missing Scenes - the script Stash runs for hooks, tasks and UI operations.

Stash runs post hooks one scene at a time before the mutation returns, so a bulk scene
update waits on this process for every scene. Loading missing_scenes (urllib, ssl,
sqlite3 and the plugin's own modules) costs most of a run, and main() then fetches the
plugin settings from Stash. A Scene.Update.Post that didn't set stash_ids changes
nothing here, so it is answered before any of that. Keep this file's imports to json
and sys.
"""

import json
import sys

SKIPPED_UPDATE = {"success": True, "message": "stash_ids not changed"}


def sets_stash_ids(hook_context):
    """True if a Scene.Update.Post's update set stash_ids (Stash's hookContext.inputFields).

    Without inputFields (a scan's update, older Stash) it didn't.
    """
    return "stash_ids" in ((hook_context or {}).get("inputFields") or [])


def early_reply(input_data):
    """The reply for a call that needs no work, or None to run the plugin."""
    args = input_data.get("args") if isinstance(input_data, dict) else None
    hook_context = args.get("hookContext") if isinstance(args, dict) else None
    if isinstance(hook_context, dict) and hook_context.get("type") == "Scene.Update.Post" \
            and not sets_stash_ids(hook_context):
        return {"output": SKIPPED_UPDATE}
    return None


def run(stdin_text):
    """Answer the call from the JSON Stash sent on stdin; prints the JSON reply."""
    try:
        input_data = json.loads(stdin_text)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON input: {e}"}))
        return

    reply = early_reply(input_data)
    if reply is not None:
        print(json.dumps(reply))
        return

    import missing_scenes

    missing_scenes.main(input_data)


if __name__ == "__main__":
    run(sys.stdin.read())
