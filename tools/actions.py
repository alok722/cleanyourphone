"""Removing and restoring packages, one at a time, journaled before every change.

The method chain, gentlest first:
  disable         `pm disable-user --user 0`  undo: `pm enable --user 0`
                  (also undoable from the phone: Settings > Apps > Disabled)
  uninstall_user  `pm uninstall -k --user 0`  undo: `cmd package install-existing --user 0`
                  (the APK stays on the system partition and the app data is kept)
  suspend         `pm suspend --user 0`       undo: `pm unsuspend --user 0`
                  (for apps the phone maker protects from both, e.g. realme's App Market)
A method only counts as done when the phone's package lists say so; many pm commands print a
success-looking line or exit 0 while changing nothing.
"""

METHODS = ("disable", "uninstall_user", "suspend")

REMOVE_CMD = {
    "disable": "pm disable-user --user 0 %s",
    "uninstall_user": "pm uninstall -k --user 0 %s",
    "suspend": "pm suspend --user 0 %s",
}
RESTORE_CMD = {
    "disable": "pm enable --user 0 %s",
    "uninstall_user": "cmd package install-existing --user 0 %s",
    "suspend": "pm unsuspend --user 0 %s",
}


def state(phone, pkg):
    """missing | uninstalled_user | disabled | suspended | enabled"""
    if pkg not in phone.packages("-u " + pkg):
        return "missing"
    if pkg not in phone.packages(pkg):
        return "uninstalled_user"
    if pkg in phone.packages("-d " + pkg):
        return "disabled"
    if "suspended=true" in phone.out("dumpsys package %s | grep -m1 -o suspended=[a-z]*" % pkg):
        return "suspended"
    return "enabled"


EFFECT = {"disable": "disabled", "uninstall_user": "uninstalled_user", "suspend": "suspended"}


def remove(phone, pkg, log, batch=None, run=None, methods=METHODS):
    """Try each method until the phone confirms one. Returns the method used, or None."""
    before = state(phone, pkg)
    for method in methods:
        log.append(action="remove", pkg=pkg, method=method, before=before, run=run, batch=batch, result="pending")
        output = phone.out(REMOVE_CMD[method] % pkg)
        after = state(phone, pkg)
        ok = after == EFFECT[method]
        log.append(action="remove", pkg=pkg, method=method, before=before, run=run, batch=batch,
                   result="ok" if ok else "refused", detail=output.splitlines()[-1][:200] if output else "")
        if ok:
            return method
        if after != before:
            # The phone changed state some other way: undo that before trying the next method.
            undo(phone, pkg, after)
    return None


def undo(phone, pkg, current):
    for method, effect in EFFECT.items():
        if effect == current:
            phone.out(RESTORE_CMD[method] % pkg)


def restore(phone, pkg, method, log):
    """Undo one journaled removal. Returns True when the package is enabled again."""
    output = phone.out(RESTORE_CMD[method] % pkg)
    ok = state(phone, pkg) == "enabled"
    log.append(action="restore", pkg=pkg, method=method, result="ok" if ok else "failed",
               detail=output.splitlines()[-1][:200] if output else "")
    return ok


def last_batch(entries):
    """Packages removed in the most recent batch that is still applied."""
    removed = [e for e in entries if e.get("action") == "remove" and e.get("result") == "ok"]
    active = dict(active_removals(entries))
    removed = [e for e in removed if e["pkg"] in active]
    if not removed:
        return set()
    key = (removed[-1].get("run"), removed[-1].get("batch"))
    return {e["pkg"] for e in removed if (e.get("run"), e.get("batch")) == key}


def active_removals(entries):
    """[(pkg, method)] removed by cleanyourphone and not restored since, oldest first."""
    current = {}
    for e in entries:
        if e.get("result") != "ok":
            continue
        if e.get("action") == "remove":
            current.pop(e["pkg"], None)
            current[e["pkg"]] = e["method"]
        elif e.get("action") == "restore":
            current.pop(e["pkg"], None)
    return list(current.items())


# ---------------------------------------------------------------- settings

def refused(output):
    return "Exception" in output or "Permission denial" in output


def set_setting(phone, ns, key, value, log, ui_recipe=None, use_ui=False):
    """Change one setting, journaled first. Returns already | put | ui | refused | ui_failed.

    `settings put` is tried first; firmware that refuses it (realme, ColorOS) gets the ui_recipe,
    a function that taps through Settings, but only when use_ui is set (the screen must be on).
    """
    before = phone.setting(ns, key)
    if before == value:
        return "already"
    log.append(action="setting", ns=ns, key=key, before=before, after=value, method="put", result="pending")
    output = phone.out("settings put %s %s %s" % (ns, key, value))
    if not refused(output) and phone.setting(ns, key) == value:
        log.append(action="setting", ns=ns, key=key, before=before, after=value, method="put", result="ok")
        return "put"
    log.append(action="setting", ns=ns, key=key, before=before, after=value, method="put", result="refused",
               detail=output.splitlines()[-1][:200] if output else "")
    if not (ui_recipe and use_ui):
        return "refused"
    try:
        ui_recipe(phone)
    except Exception as e:  # the UI helper raises on anything unexpected on screen
        log.append(action="setting", ns=ns, key=key, before=before, after=value, method="ui", result="failed",
                   detail=str(e)[:200])
        return "ui_failed"
    ok = phone.setting(ns, key) == value
    log.append(action="setting", ns=ns, key=key, before=before, after=value, method="ui",
               result="ok" if ok else "failed")
    return "ui" if ok else "ui_failed"


def restore_setting(phone, ns, key, before, log):
    """Put a setting back. Returns True when the phone reads the old value again."""
    if before is None:
        output = phone.out("settings delete %s %s" % (ns, key))
    else:
        output = phone.out("settings put %s %s %s" % (ns, key, before))
    ok = phone.setting(ns, key) == before
    log.append(action="restore_setting", ns=ns, key=key, before=before, result="ok" if ok else "failed",
               detail=output.splitlines()[-1][:200] if output else "")
    return ok


def active_settings(entries):
    """[(ns, key, before)] changed by cleanyourphone and not put back since, oldest first."""
    current = {}
    for e in entries:
        if e.get("result") != "ok":
            continue
        if e.get("action") == "setting":
            k = (e["ns"], e["key"])
            # Keep the earliest "before": that is the phone's own value.
            current.setdefault(k, e["before"])
        elif e.get("action") == "restore_setting":
            current.pop((e["ns"], e["key"]), None)
    return [(ns, key, before) for (ns, key), before in current.items()]
