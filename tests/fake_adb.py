"""A stand-in for `adb` that behaves like a recorded phone, so the tools can be tested without one.

Point CYP_ADB at this file. It reads tests/fixtures/<CYP_FAKE_PHONE or realme7>.json:
    devices   `adb devices -l` output
    packages  {"system": [...], "user": [...]}       what `pm list packages` reports
    disabled  packages disabled before the test starts
    refuse    {"disable": [...], "uninstall": [...]}  packages the firmware protects (realme's App Market)
    breaks    {"<package>": "home"}                   removing it makes the home screen stop resolving
    settings  {"<namespace>/<key>": "<value>"}        `settings get/put/delete`; CYP_FAKE_REFUSE_SETTINGS=1
                                                      refuses writes the way realme UI does
    shell     {"<command>": "<output>"}               everything else, replayed verbatim
CYP_FAKE_LOCKED=1 shows the lock screen.
Package changes (disable, uninstall for user, suspend and their undos) persist in $CYP_FAKE_STATE
between calls, and refusals print the strings a realme 7 printed. Every call is appended to
$CYP_FAKE_LOG so tests can assert what was run.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(data):
    path = os.environ.get("CYP_FAKE_STATE")
    if path and os.path.exists(path):
        with open(path) as f:
            raw = json.load(f)
        return {k: (v if k in ("settings", "buckets") else set(v)) for k, v in raw.items()}
    return {"disabled": set(data.get("disabled", [])), "uninstalled": set(), "suspended": set(),
            "settings": dict(data.get("settings", {})), "buckets": dict(data.get("buckets", {}))}


def save(state):
    path = os.environ.get("CYP_FAKE_STATE")
    if path:
        with open(path, "w") as f:
            json.dump({k: (v if k in ("settings", "buckets") else sorted(v)) for k, v in state.items()}, f)


def list_packages(data, state, args):
    flags = [a for a in args if a.startswith("-")]
    filt = next((a for a in args if not a.startswith("-")), "")
    system, user = data["packages"]["system"], data["packages"]["user"]
    names = system if "-s" in flags else user if "-3" in flags else system + user
    rows = []
    for name in names:
        if name in state["uninstalled"] and "-u" not in flags:
            continue
        if "-d" in flags and name not in state["disabled"]:
            continue
        if "-e" in flags and name in state["disabled"]:
            continue
        if filt in name:
            rows.append("package:" + ("/system/app/%s/base.apk=%s" % (name, name) if "-f" in flags else name))
    return "\n".join(rows) + ("\n" if rows else "")


def shell(data, state, cmd):
    """(stdout, exit code) for one shell command."""
    refuse = data.get("refuse", {})
    m = re.fullmatch(r"pm list packages ?(.*)", cmd)
    if m:
        return list_packages(data, state, m.group(1).split()), 0
    m = re.fullmatch(r"pm disable-user --user 0 (\S+)", cmd)
    if m:
        pkg = m.group(1)
        if pkg in refuse.get("disable", []):
            return "Package %s new state: default\n" % pkg, 0
        state["disabled"].add(pkg)
        return "Package %s new state: disabled-user\n" % pkg, 0
    m = re.fullmatch(r"pm enable --user 0 (\S+)", cmd)
    if m:
        state["disabled"].discard(m.group(1))
        return "Package %s new state: enabled\n" % m.group(1), 0
    m = re.fullmatch(r"pm uninstall -k --user 0 (\S+)", cmd)
    if m:
        if m.group(1) in refuse.get("uninstall", []):
            return "Failure [DELETE_FAILED_INTERNAL_ERROR]\n", 0
        state["uninstalled"].add(m.group(1))
        return "Success\n", 0
    m = re.fullmatch(r"cmd package install-existing --user 0 (\S+)", cmd)
    if m:
        state["uninstalled"].discard(m.group(1))
        return "Package %s installed for user: 0\n" % m.group(1), 0
    m = re.fullmatch(r"pm (un)?suspend --user 0 (\S+)", cmd)
    if m:
        (state["suspended"].discard if m.group(1) else state["suspended"].add)(m.group(2))
        return "Package %s new suspended state: %s\n" % (m.group(2), "false" if m.group(1) else "true"), 0
    m = re.fullmatch(r"dumpsys package (\S+) \| grep .*suspended.*", cmd)
    if m:
        return "suspended=%s\n" % ("true" if m.group(1) in state["suspended"] else "false"), 0
    m = re.fullmatch(r"settings (get|put|delete) (\S+) (\S+) ?(\S*)", cmd)
    if m:
        verb, ns, key, value = m.groups()
        if verb == "get":
            return state["settings"].get(ns + "/" + key, "null") + "\n", 0
        if os.environ.get("CYP_FAKE_REFUSE_SETTINGS"):
            return ("Exception occurred while executing 'put':\njava.lang.SecurityException: Permission denial: "
                    "writing to settings requires:android.permission.WRITE_SECURE_SETTINGS\n"), 255
        if verb == "put":
            state["settings"][ns + "/" + key] = value
        else:
            state["settings"].pop(ns + "/" + key, None)
        return "", 0
    m = re.fullmatch(r"ping -c 1 -W 3 (\S+)", cmd)
    if m:
        dns_on = state["settings"].get("global/private_dns_mode") == "hostname"
        ip = "0.0.0.0" if dns_on and m.group(1) == "doubleclick.net" else "203.0.113.7"
        return "PING %s (%s) 56(84) bytes of data.\n" % (m.group(1), ip), 0
    if cmd == "dumpsys usagestats":
        rows = "".join('      package=%s totalTimeUsed="01:00" lastTimeUsed="%s" totalTimeVisible="01:00"\n' % kv
                       for kv in data.get("last_used", {}).items())
        return "  In-memory daily stats\n  In-memory yearly stats\n" + rows, 0
    if cmd.startswith("cmd package query-activities") and "LAUNCHER" in cmd:
        gone = state["disabled"] | state["uninstalled"]
        return "".join("%s/.Main\n" % p for p in data.get("launcher", []) if p not in gone), 0
    m = re.fullmatch(r"dumpsys package (\S+) \| grep -m1 firstInstallTime", cmd)
    if m:
        t = data.get("installed_at", {}).get(m.group(1))
        return ("    firstInstallTime=%s\n" % t) if t else "", 0
    m = re.fullmatch(r"am get-standby-bucket (\S+)", cmd)
    if m:
        return "%s\n" % state["buckets"].get(m.group(1), 10), 0
    m = re.fullmatch(r"am set-standby-bucket (\S+) (\S+)", cmd)
    if m:
        names = {"active": 10, "working_set": 20, "frequent": 30, "rare": 40, "restricted": 45}
        state["buckets"][m.group(1)] = names[m.group(2)]
        return "", 0
    if cmd == "dumpsys window":
        return "    isKeyguardShowing=%s\n" % ("true" if os.environ.get("CYP_FAKE_LOCKED") else "false"), 0
    if cmd.startswith("cmd package resolve-activity"):
        gone = state["disabled"] | state["uninstalled"] | state["suspended"]
        if any(pkg in gone and what == "home" for pkg, what in data.get("breaks", {}).items()):
            return "No activity found\n", 0
    return data["shell"].get(cmd, ""), 0


def main(argv):
    fixture = os.environ.get("CYP_FAKE_PHONE", "realme7")
    with open(os.path.join(HERE, "fixtures", fixture + ".json")) as f:
        data = json.load(f)
    if argv[:1] == ["-s"]:
        argv = argv[2:]
    log = os.environ.get("CYP_FAKE_LOG")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps(argv) + "\n")
    if argv[:1] == ["version"]:
        print("Android Debug Bridge version 1.0.41 (fake)")
    elif argv[:1] == ["devices"]:
        sys.stdout.write(data["devices"])
    elif argv[:1] == ["shell"]:
        state = load(data)
        out, code = shell(data, state, " ".join(argv[1:]))
        save(state)
        sys.stdout.write(out)
        return code
    elif argv[:2] == ["exec-out", "screencap"]:
        sys.stdout.buffer.write(b"\x89PNG\r\n\x1a\nfake")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
