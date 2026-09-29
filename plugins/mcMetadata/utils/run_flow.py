"""Run-flow decisions made before any logging happens."""


def is_disabled_hook_run(mode, settings):
    """True when this invocation is a hook whose gate is off.

    Such runs must be silent no-ops: no log file created or truncated, no INFO output.
    Scene.Update.Post is gated by enable_hook; Performer.Update.Post by enable_actor_images.
    """
    if mode == "Scene.Update.Post":
        return not settings.get("enable_hook", False)
    if mode == "Performer.Update.Post":
        return not settings.get("enable_actor_images", False)
    return False
