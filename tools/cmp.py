"""cleanyourphone command line: `./cmp <step>` (or `cmp.ps1 <step>` on Windows).

Every step ends with the same footer so any coding assistant, even a small free one, can drive it:

    RESULT: ok | warn | stop
    SUMMARY: one line
    NEXT: the exact command to run next
    ASK_USER: a question to ask the person verbatim (only when a human has to decide or act)

Exit code: 0 for ok and warn, 2 for stop.
"""
import argparse
import re
import json
import os
import platform
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import actions  # noqa: E402
import bench  # noqa: E402
import checks  # noqa: E402
import journal  # noqa: E402
import kb  # noqa: E402
import safety  # noqa: E402
import tune  # noqa: E402
import ui  # noqa: E402
import usage  # noqa: E402
from phone import AdbError, Phone, find_adb, list_devices, run_adb  # noqa: E402

VERSION = "0.1.0"
CMD = "cmp.ps1" if os.name == "nt" else "./cmp"

SKIN_PROPS = [
    ("ro.build.version.realmeui", "realme UI"),
    ("ro.build.version.oplusrom", "ColorOS"),
    ("ro.build.version.opporom", "ColorOS"),
    ("ro.oxygen.version", "OxygenOS"),
    ("ro.mi.os.version.name", "HyperOS"),
    ("ro.miui.ui.version.name", "MIUI"),
    ("ro.build.version.oneui", "One UI"),
    ("ro.vivo.os.name", "Funtouch/OriginOS"),
    ("ro.build.version.emui", "EMUI"),
]


class Footer:
    """Collects the result of a step and prints the footer the prompt relies on."""

    def __init__(self):
        self.result = "ok"
        self.summary = ""
        self.next = None
        self.ask = None

    def warn(self, msg):
        print("WARN: " + msg)
        if self.result == "ok":
            self.result = "warn"

    def stop(self, summary, next_cmd=None, ask=None):
        self.result, self.summary, self.next, self.ask = "stop", summary, next_cmd, ask
        return self

    def emit(self):
        print()
        print("RESULT: " + self.result)
        print("SUMMARY: " + (self.summary or "-"))
        if self.next:
            print("NEXT: " + self.next)
        if self.ask:
            print("ASK_USER: " + self.ask)
        return 2 if self.result == "stop" else 0


def install_hint():
    system = platform.system()
    if system == "Darwin":
        if not shutil.which("brew"):
            return ('/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
                    " && brew install --cask android-platform-tools")
        return "brew install --cask android-platform-tools"
    if system == "Windows":
        return "winget install --id Google.PlatformTools -e"
    if shutil.which("dnf"):
        return "sudo dnf install -y android-tools"
    if shutil.which("pacman"):
        return "sudo pacman -S --noconfirm android-tools"
    return "sudo apt-get install -y adb"


def pick_phone(footer):
    """The phone to work on, or None after setting a stop on the footer."""
    if not find_adb():
        footer.stop("adb is not installed", CMD + " doctor")
        return None
    try:
        devices = list_devices()
    except AdbError as e:
        footer.stop("adb failed: %s" % e, CMD + " doctor")
        return None
    ready = [d for d in devices if d[1] == "device"]
    wanted = os.environ.get("CYP_SERIAL") or journal.load_current()
    for serial, _, _ in ready:
        if serial == wanted:
            return Phone(serial)
    if len(ready) == 1:
        return Phone(ready[0][0])
    if not ready:
        footer.stop("no phone connected", CMD + " connect")
    else:
        footer.stop("%d phones connected" % len(ready), CMD + " connect",
                    "Which phone should I work on? Set CYP_SERIAL to its serial and re-run.")
    return None


# ---------------------------------------------------------------- doctor

def cmd_doctor(args, footer):
    print("Python %s on %s" % (platform.python_version(), platform.system()))
    if sys.version_info < (3, 8):
        footer.stop("Python 3.8 or newer is needed", None, "Please install Python 3.8+ and re-run.")
        return
    adb = find_adb()
    if not adb:
        # Install it without asking: the person opted in by running cleanyourphone.
        footer.stop("adb is not installed; install it, then re-run doctor",
                    "%s && %s doctor" % (install_hint(), CMD))
        return
    rc, out, _ = run_adb(["version"])
    print("adb: %s (%s)" % (adb, out.splitlines()[0] if out else "unknown version"))
    devices = list_devices()
    for serial, state, details in devices:
        print("device %s  %s  %s" % (mask_serial(serial), state, details.get("model", "")))
    if not devices:
        footer.stop("adb works, no phone connected yet", CMD + " connect",
                    "Connect the phone with a USB data cable and turn on Settings > Developer options > "
                    "USB debugging. Tell me when it is plugged in.")
        return
    if any(state == "unauthorized" for _, state, _ in devices):
        footer.stop("phone found but not authorised", CMD + " doctor",
                    "Unlock the phone and tap 'Allow' on the 'Allow USB debugging?' prompt "
                    "(tick 'Always allow from this computer'). Tell me when done.")
        return
    footer.summary = "Python, adb and %d phone(s) ready" % len(devices)
    footer.next = CMD + " connect"


def mask_serial(serial):
    """Show only the start of a serial in output people may paste publicly."""
    return serial[:4] + "…" if len(serial) > 6 and ":" not in serial else serial


# ---------------------------------------------------------------- connect

def cmd_connect(args, footer):
    if args.pair:
        host, code = args.pair
        rc, out, err = run_adb(["pair", host, code], timeout=60)
        print((out + err).strip())
        if rc != 0 or "Successfully" not in out + err:
            footer.stop("pairing failed", None,
                        "Pairing didn't work. On the phone open Developer options > Wireless debugging > "
                        "Pair device with pairing code, and tell me the new IP:port and 6-digit code.")
            return
    if args.wifi:
        rc, out, err = run_adb(["connect", args.wifi], timeout=30)
        print((out + err).strip())
    phone = pick_phone(footer)
    if not phone:
        if footer.summary == "no phone connected":
            footer.ask = ("Connect the phone with a USB data cable (or use Wireless debugging) with USB debugging "
                          "turned on, then tell me. If nothing shows up, try another cable and pick "
                          "'File transfer' in the phone's USB notification.")
            footer.next = CMD + " connect"
        return
    journal.save_current(phone.serial)
    model = phone.getprop("ro.product.model")
    print("Connected: %s %s (device id %s)" % (phone.getprop("ro.product.manufacturer"), model, phone.id))
    footer.summary = "connected to %s" % model
    footer.next = CMD + " probe"


# ---------------------------------------------------------------- probe

def skin(phone):
    for prop, name in SKIN_PROPS:
        value = phone.getprop(prop)
        if value:
            return "%s %s" % (name, value)
    return "stock-like Android"


def meminfo(phone):
    info = {}
    for line in phone.out("cat /proc/meminfo").splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():
            info[parts[0]] = int(parts[1])
    return info


def data_storage(phone):
    """(total_kb, free_kb) of /data, or (None, None)."""
    for line in phone.out("df -k /data").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[1].isdigit():
            return int(parts[1]), int(parts[3])
    return None, None


def probe_info(phone):
    mem = meminfo(phone)
    total, free = data_storage(phone)
    uptime = phone.out("cat /proc/uptime").split()
    battery = {}
    for line in phone.out("dumpsys battery").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            battery[k.strip()] = v.strip()
    return {
        "manufacturer": phone.getprop("ro.product.manufacturer"),
        "model": phone.getprop("ro.product.model"),
        "device": phone.getprop("ro.product.device"),
        "android": phone.getprop("ro.build.version.release"),
        "sdk": int(phone.getprop("ro.build.version.sdk") or 0),
        "skin": skin(phone),
        "build": phone.getprop("ro.build.display.id"),
        "security_patch": phone.getprop("ro.build.version.security_patch"),
        "abi": phone.getprop("ro.product.cpu.abi"),
        "ram_total_mb": mem.get("MemTotal", 0) // 1024,
        "ram_available_mb": mem.get("MemAvailable", 0) // 1024,
        "storage_total_gb": round(total / 1048576, 1) if total else None,
        "storage_free_gb": round(free / 1048576, 1) if free else None,
        "uptime_days": round(float(uptime[0]) / 86400, 1) if uptime else None,
        "battery_level": int(battery.get("level", "0") or 0),
        "battery_saver": phone.setting("global", "low_power") == "1",
        "packages_total": len(phone.packages()),
        "packages_system": len(phone.packages("-s")),
        "packages_user": len(phone.packages("-3")),
        "packages_disabled": len(phone.packages("-d")),
    }


def cmd_probe(args, footer):
    phone = pick_phone(footer)
    if not phone:
        return
    info = probe_info(phone)
    for key, value in info.items():
        print("%-18s %s" % (key, value))
    if info["sdk"] and info["sdk"] < 24:
        footer.warn("Android %s is older than 7.0: debloat works best effort, speed tuning and roles may not"
                    % info["android"])
    if info["battery_saver"]:
        footer.warn("Battery Saver is on: it throttles the CPU and caps the refresh rate, so the phone feels "
                    "slower and benchmarks read low")
    if info["battery_level"] and info["battery_level"] < 20:
        footer.warn("battery at %d%%: keep the phone charging during the cleanup" % info["battery_level"])
    if info["storage_total_gb"] and info["storage_free_gb"] is not None \
            and info["storage_free_gb"] / info["storage_total_gb"] < 0.10:
        footer.warn("less than 10% storage free: a nearly full phone is slow whatever we remove")
    if info["uptime_days"] and info["uptime_days"] > 7:
        footer.warn("not restarted for %.0f days: a reboot alone will help" % info["uptime_days"])
    footer.summary = "%s %s, Android %s, %s" % (info["manufacturer"], info["model"], info["android"], info["skin"])
    footer.next = CMD + " backup"


# ---------------------------------------------------------------- backup

ROLES = ["android.app.role.HOME", "android.app.role.SMS", "android.app.role.DIALER",
         "android.app.role.ASSISTANT", "android.app.role.BROWSER"]


def cmd_backup(args, footer):
    phone = pick_phone(footer)
    if not phone:
        return
    path = journal.new_backup_dir(phone.id)
    files = {
        "packages-all.txt": "pm list packages -u",
        "packages-installed.txt": "pm list packages",
        "packages-system.txt": "pm list packages -s",
        "packages-user.txt": "pm list packages -3",
        "packages-disabled.txt": "pm list packages -d",
        "packages-paths.txt": "pm list packages -f -u",
        "settings-global.txt": "settings list global",
        "settings-secure.txt": "settings list secure",
        "settings-system.txt": "settings list system",
        "getprop.txt": "getprop",
    }
    for name, cmd in files.items():
        with open(os.path.join(path, name), "w") as f:
            f.write(phone.out(cmd, timeout=120) + "\n")
    roles = {role: phone.role_holders(role) for role in ROLES}
    roles["default_input_method"] = phone.setting("secure", "default_input_method")
    meta = probe_info(phone)
    meta.update({"device_id": phone.id, "roles": roles, "tool_version": VERSION})
    with open(os.path.join(path, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
    with open(os.path.join(path, "packages-all.txt")) as f:
        count = len(f.read().split())
    print("Backup written to %s" % path)
    print("  %d packages, settings (global/secure/system), properties, role holders" % count)
    journal.State(phone.id).update(last_backup=os.path.basename(path))
    footer.summary = "backup saved (%d packages)" % count
    footer.next = CMD + " discover"


# ---------------------------------------------------------------- status

def cmd_status(args, footer):
    phone = pick_phone(footer)
    if not phone:
        return
    state = journal.State(phone.id).load()
    entries = journal.Journal(phone.id).entries()
    backups = journal.backups(phone.id)
    print("phone        %s (device id %s)" % (phone.getprop("ro.product.model"), phone.id))
    print("backups      %d%s" % (len(backups), " (latest %s)" % os.path.basename(backups[-1]) if backups else ""))
    print("changes      %d journaled" % len(entries))
    for key in ("last_verify", "last_batch"):
        if key in state:
            print("%-12s %s" % (key, state[key]))
    if not backups:
        footer.summary = "no backup yet"
        footer.next = CMD + " backup"
    else:
        footer.summary = "%d backup(s), %d change(s) journaled" % (len(backups), len(entries))


# ---------------------------------------------------------------- discover

def cmd_discover(args, footer):
    phone = pick_phone(footer)
    if not phone:
        return
    known = kb.load()
    never = safety.load_never()
    protected = safety.protected_now(phone)
    # Only preinstalled (system) apps: an app the owner installed is never bloat, even when the
    # same package ships preinstalled elsewhere (LinkedIn on Samsung).
    installed, disabled = phone.packages("-s"), phone.packages("-d")
    model = phone.getprop("ro.product.model")
    exact, meta = kb.matching_device(model)
    groups, kept = {}, []
    for pkg in sorted(installed - disabled):
        if pkg not in known or safety.never_reason(pkg, never):
            continue
        if pkg in protected:
            kept.append((pkg, protected[pkg]))
            continue
        if actions.state(phone, pkg) == "suspended":
            continue
        groups.setdefault(known[pkg]["group"], []).append(pkg)
    for pkg, why in kept:
        print("  keep   %-48s %s" % (pkg, why))
    if exact:
        print("This exact model has a %s list: devices/%s" % (meta.get("status"), exact))
    total = sum(len(v) for v in groups.values())
    if not total:
        footer.summary = "no known bloat left on this phone (%d apps checked)" % len(installed)
        return
    lines = ["# cleanyourphone proposal for %s %s, %s" % (phone.getprop("ro.product.manufacturer"), model,
                                                          journal.now()),
             "# Written by `cmp discover` from the lists in devices/. Comment out (#) anything you want to keep.",
             "# Apply: %s apply %s" % (CMD, args.out), ""]
    print("\nFound %d app(s) you may not need:" % total)
    for group, pkgs in groups.items():
        print("  %-52s %d  (%s%s)" % (group, len(pkgs), ", ".join(pkgs[:2]), ", ..." if len(pkgs) > 2 else ""))
        lines.append("# " + group)
        for pkg in pkgs:
            entry = known[pkg]
            note = entry["note"]
            if entry["status"] != "tested":
                note = (note + "; " if note else "") + "from an untested starter list"
            lines.append("%-46s # %s" % (pkg, note) if note else pkg)
        lines.append("")
    with open(args.out, "w") as f:
        f.write("\n".join(lines))
    print("\nProposal written to %s" % args.out)
    footer.summary = "%d app(s) in %d group(s) proposed in %s; nothing changed yet" % (total, len(groups), args.out)
    footer.next = "%s apply %s" % (CMD, args.out)
    footer.ask = ("I found %d app(s) you may not need, in these groups: %s. Which groups or apps do you want to "
                  "keep? Everything else gets removed in small batches, and all of it can be restored." % (
                      total, "; ".join("%s (%d)" % (g, len(p)) for g, p in groups.items())))


# ---------------------------------------------------------------- apply

def plan_list(phone, path):
    """(todo, skipped) for a debloat list on this phone; skipped is [(pkg, why)]."""
    protected = safety.protected_now(phone)
    installed, everything = phone.packages(), phone.packages("-u")
    disabled = phone.packages("-d")
    todo, skipped = [], []
    for pkg in safety.read_list(path):
        if pkg in protected:
            skipped.append((pkg, protected[pkg] + ", kept"))
        elif pkg not in everything:
            skipped.append((pkg, "not on this phone"))
        elif pkg not in installed or pkg in disabled or actions.state(phone, pkg) == "suspended":
            skipped.append((pkg, "already off"))
        else:
            todo.append(pkg)
    return todo, skipped


def cmd_apply(args, footer):
    problems = safety.lint_list(args.list)
    if problems:
        for no, pkg, problem in problems:
            print("%s:%d: %s: %s" % (args.list, no, pkg, problem))
        footer.stop("the list names packages cleanyourphone never touches; nothing was changed")
        return
    phone = pick_phone(footer)
    if not phone:
        return
    if not journal.backups(phone.id):
        footer.stop("no backup yet; nothing was changed", CMD + " backup")
        return
    todo, skipped = plan_list(phone, args.list)
    for pkg, why in skipped:
        print("  skip   %-48s %s" % (pkg, why))
    if not todo:
        footer.summary = "nothing to do: every package in the list is already off or not on this phone"
        footer.next = CMD + " verify"
        return
    size = max(1, min(args.batch, 10))
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    if not args.yes:
        for pkg in todo:
            print("  remove %s" % pkg)
        footer.summary = "%d app(s) to remove in %d batch(es) of up to %d; nothing changed yet" % (
            len(todo), len(batches), size)
        footer.next = "%s apply %s --yes" % (CMD, args.list)
        footer.ask = ("I'm about to remove %d apps in batches of %d, checking the phone after each batch. Every "
                      "step can be undone with '%s restore'. Go ahead?" % (len(todo), size, CMD))
        return

    log = journal.Journal(phone.id)
    state = journal.State(phone.id)
    run_id = journal.now()
    baseline = checks.run(phone)
    print("Health before:")
    checks.report(baseline)
    done = {"disable": 0, "uninstall_user": 0, "suspend": 0, "failed": 0}
    for n, batch in enumerate(batches, 1):
        since = checks.phone_time(phone)
        print("\nBatch %d/%d" % (n, len(batches)))
        used = []
        for pkg in batch:
            method = actions.remove(phone, pkg, log, batch=n, run=run_id)
            print("  %-48s %s" % (pkg, method or "REFUSED by the phone, left as it was"))
            done[method or "failed"] += 1
            if method:
                used.append((pkg, method))
        checks.settle()
        current = checks.run(phone, since=since)
        broken = checks.regressions(baseline, current)
        if args.shots:
            phone.screenshot(os.path.join("shots", "%s-batch%02d.png" % (journal.now(), n)))
        if broken:
            checks.report(current)
            print("Batch %d broke: %s. Undoing this batch..." % (n, ", ".join(name for name, _ in broken)))
            for pkg, method in reversed(used):
                actions.restore(phone, pkg, method, log)
            state.update(last_batch="%d/%d rolled back" % (n, len(batches)))
            footer.stop("batch %d broke the phone's %s and was undone; earlier batches are still applied. "
                        "One of these is load-bearing here: %s" % (n, broken[0][0], ", ".join(batch)),
                        None, "Batch %d broke something and I undid it. Shall I try that batch one app at a time "
                        "to find the culprit?" % n)
            return
        print("  checks ok")
        state.update(last_batch="%d/%d" % (n, len(batches)))
    footer.summary = "%d disabled, %d uninstalled for user, %d suspended, %d refused, %d skipped" % (
        done["disable"], done["uninstall_user"], done["suspend"], done["failed"], len(skipped))
    if done["failed"]:
        footer.warn("%d app(s) refused every method; they are unchanged" % done["failed"])
    footer.next = CMD + " verify --reboot"


# ---------------------------------------------------------------- verify

def cmd_verify(args, footer):
    phone = pick_phone(footer)
    if not phone:
        return
    expected = actions.active_removals(journal.Journal(phone.id).entries())
    if args.reboot:
        print("Rebooting (waiting 15 s first so Android writes package state to disk)...")
        time.sleep(15)
        if not phone.reboot_and_wait():
            footer.stop("the phone did not finish booting within 5 minutes", None,
                        "The phone hasn't finished booting. Is it stuck on the logo? If it is, tell me and "
                        "I'll walk you through getting it back.")
            return
    results = checks.run(phone)
    print("Health:")
    checks.report(results)
    back = [pkg for pkg, method in expected
            if actions.state(phone, pkg) == "enabled"]
    if back:
        footer.warn("came back on their own (the firmware re-enables them): " + ", ".join(back))
    failed = [name for name, ok, _ in results if not ok and name not in ("SIM loaded",)]
    journal.State(phone.id).update(last_verify="%s %s" % (journal.now(), "ok" if not failed else "failed"))
    if failed:
        footer.stop("checks failing: " + ", ".join(failed), CMD + " restore --last",
                    "Something isn't right (%s). Shall I undo the last batch?" % ", ".join(failed))
        return
    footer.summary = "phone healthy%s; %d app(s) removed by cleanyourphone" % (
        " after reboot" if args.reboot else "", len(expected))


# ---------------------------------------------------------------- restore

def cmd_restore(args, footer):
    phone = pick_phone(footer)
    if not phone:
        return
    log = journal.Journal(phone.id)
    entries = log.entries()
    active = actions.active_removals(entries)
    if args.packages:
        active = [(p, m) for p, m in active if p in args.packages]
        unknown = set(args.packages) - {p for p, _ in active}
        for pkg in sorted(unknown):
            print("  %s was not removed by cleanyourphone on this phone; left alone" % pkg)
    elif args.last:
        last = actions.last_batch(entries)
        active = [(p, m) for p, m in active if p in last]
    settings = [] if (args.packages or args.last) else actions.active_settings(entries)
    buckets = [] if args.last else [(p, b) for p, b in usage.active_restrictions(entries)
                                    if not args.packages or p in args.packages]
    if not active and not settings and not buckets:
        footer.summary = "nothing to restore"
        return
    restored, failed = 0, []
    for pkg, before in buckets:
        ok = usage.unrestrict(phone, pkg, before, log)
        print("  %-48s %s" % (pkg, "background allowed again" if ok else "FAILED"))
        restored += 1 if ok else 0
    for ns, key, before in settings:
        ok = actions.restore_setting(phone, ns, key, before, log)
        print("  %-48s %s" % ("%s %s" % (ns, key), "back to %s" % before if ok else "FAILED"))
        if ok:
            restored += 1
        else:
            failed.append("%s (set it back in Settings)" % key)
    for pkg, method in reversed(active):
        ok = actions.restore(phone, pkg, method, log)
        print("  %-48s %s" % (pkg, "back" if ok else "FAILED"))
        if ok:
            restored += 1
        else:
            failed.append(pkg)
    if failed:
        footer.warn("could not restore: %s. An app you installed yourself: reinstall it from the Play Store "
                    "(its data was kept). A preinstalled app: Settings > Apps, or a factory reset, brings it back"
                    % ", ".join(failed))
    footer.summary = "%d change(s) restored" % restored
    footer.next = CMD + " verify"


# ---------------------------------------------------------------- tune

UNLOCK_ASK = ("Your phone blocks these changes over USB, so I'll tap through Settings myself. Unlock the phone, "
              "keep the screen on and don't touch it for about a minute. Tell me when it's ready.")


def screen_ask(phone, ask):
    """Prefix a screen-needing question with a longer-timeout request when the phone locks quickly."""
    timeout = phone.setting("system", "screen_off_timeout")
    if timeout and timeout.isdigit() and int(timeout) < 120000:
        return ("Your screen turns off after %d seconds, which locks the phone mid-step. First set Settings > "
                "Display > Screen timeout (Auto screen off) to 5 minutes; you can change it back afterwards. "
                % (int(timeout) // 1000)) + ask
    return ask


def cmd_tune(args, footer):
    phone = pick_phone(footer)
    if not phone:
        return
    if not journal.backups(phone.id):
        footer.stop("no backup yet; nothing was changed", CMD + " backup")
        return
    if args.ui and not ui.unlocked(phone):
        footer.stop("the phone is locked", CMD + " tune --ui", screen_ask(phone, UNLOCK_ASK))
        return
    if phone.setting("global", "low_power") == "1":
        footer.warn("Battery Saver is on: it caps the refresh rate and slows the CPU whatever we tune; "
                    "turn it off once the phone is charged")
    log = journal.Journal(phone.id)
    blocked = set()
    for ns, key, value, recipe, what in tune.speed_targets(phone, skin(phone)):
        result = actions.set_setting(phone, ns, key, value, log, ui_recipe=recipe, use_ui=args.ui)
        print("  %-28s -> %-6s %s" % (key, value, {"already": "already set", "put": "set", "ui": "set via Settings",
                                                   "refused": "blocked by the phone",
                                                   "ui_failed": "couldn't set it by tapping"}[result]))
        if result in ("refused", "ui_failed"):
            blocked.add((what, recipe is not None and result == "refused"))
    if not args.ui:
        freed = tune.trim_caches(phone)
        print("  app caches cleared: about %d MB freed" % freed)
    if args.deep:
        print("  recompiling apps and running storage maintenance (can take 10+ minutes)...")
        tune.deep_maintenance(phone)
    taps = [what for what, has_recipe in blocked if has_recipe]
    manual = [what for what, has_recipe in blocked if not has_recipe]
    if taps:
        footer.summary = "some settings need tapping through Settings: " + ", ".join(sorted(set(taps)))
        footer.next = CMD + " tune --ui"
        footer.ask = screen_ask(phone, UNLOCK_ASK)
        return
    if manual:
        footer.warn("set these by hand on the phone: " + " | ".join(tune.MANUAL[w] for w in sorted(set(manual))))
        footer.ask = "Please make this change on the phone: %s. Tell me when done." % "; ".join(
            tune.MANUAL[w] for w in sorted(set(manual)))
    footer.summary = "speed settings done" if not manual else "speed settings done except: " + ", ".join(manual)
    footer.next = CMD + " adblock"


# ---------------------------------------------------------------- adblock

def cmd_adblock(args, footer):
    phone = pick_phone(footer)
    if not phone:
        return
    mode = phone.setting("global", "private_dns_mode")
    host = phone.setting("global", "private_dns_specifier")
    sdk = int(phone.getprop("ro.build.version.sdk") or 0)
    print("Private DNS now: %s%s" % (mode or "default (automatic)", " (%s)" % host if mode == "hostname" else ""))
    if sdk and sdk < 28:
        footer.warn("Private DNS needs Android 9+; on this phone use a local VPN blocker such as RethinkDNS")
        footer.summary = "Private DNS not available on Android %s" % phone.getprop("ro.build.version.release")
        footer.next = CMD + " bench"
        return
    if args.check:
        blocked, online = tune.ads_blocked(phone)
        print("  ad domain blocked:  %s" % ("yes" if blocked else "NO"))
        print("  internet working:   %s" % ("yes" if online else "NO"))
        if not online:
            footer.stop("the internet stopped resolving with this DNS", CMD + " adblock --off",
                        "Websites aren't loading with the ad-blocking DNS. Shall I switch it off?")
        elif not blocked:
            footer.stop("ads are not blocked yet", CMD + " adblock --check",
                        "Ads aren't blocked yet. Did you save the Private DNS hostname? Tell me when it's saved.")
        else:
            footer.summary = "ads blocked system-wide via %s" % host
            footer.next = CMD + " bench --label after"
        return
    if args.off:
        log = journal.Journal(phone.id)
        result = actions.set_setting(phone, "global", "private_dns_mode", "opportunistic", log)
        if result == "refused":
            footer.ask = "Please open Settings, search 'Private DNS', choose 'Automatic' and save. Tell me when done."
        footer.summary = "ad-blocking DNS off" if result != "refused" else "switch Private DNS back to Automatic by hand"
        return
    if not args.provider:
        for step in tune.oem_ad_switches(skin(phone)):
            print("  phone setting to switch off: " + step)
        footer.summary = "ad blocking not set up yet; nothing changed"
        footer.next = CMD + " adblock --provider adguard"
        footer.ask = ("Want system-wide ad blocking? I'd set Private DNS to AdGuard DNS (free). It blocks ads and "
                      "trackers in apps and browsers, but not YouTube ads, and AdGuard sees which sites the phone "
                      "looks up. Skip it if your router or a VPN already filters DNS (Pi-hole, AdGuard Home). "
                      "Also, should I walk you through the phone's own ad switches listed above?")
        return
    target, label = tune.PROVIDERS[args.provider]
    log = journal.Journal(phone.id)
    r1 = actions.set_setting(phone, "global", "private_dns_specifier", target, log)
    r2 = actions.set_setting(phone, "global", "private_dns_mode", "hostname", log)
    print("  %s" % label)
    if "refused" in (r1, r2):
        manual = tune.PRIVATE_DNS_MANUAL.format(host=target)
        footer.summary = "the phone blocks this change over USB; it needs two taps in Settings"
        footer.next = CMD + " adblock --check"
        footer.ask = "Please do this on the phone: %s. Tell me when it's saved." % manual
        return
    footer.summary = "Private DNS set to %s" % target
    footer.next = CMD + " adblock --check"


# ---------------------------------------------------------------- bench / report

def bench_dir(phone_id):
    return os.path.join(journal.device_dir(phone_id), "bench")


def cmd_bench(args, footer):
    phone = pick_phone(footer)
    if not phone:
        return
    interactive = not args.quick
    if interactive and not ui.unlocked(phone):
        footer.stop("the phone is locked", "%s bench --label %s" % (CMD, args.label), screen_ask(phone,
                    "To time app starts and scrolling I need the phone unlocked with the screen on for about two "
                    "minutes. Unlock it, don't touch it, and tell me when it's ready."))
        return
    data = bench.measure(phone, interactive=interactive)
    path = bench.save(data, bench_dir(phone.id), args.label)
    for key in ("ram_available_mb", "swap_used_mb", "processes", "packages_installed", "animation_scale",
                "refresh_rate", "private_dns", "cold_start_ms", "janky_pct"):
        if key in data:
            print("  %-22s %s" % (key, data[key]))
    if data["battery_saver"]:
        footer.warn("Battery Saver is on: numbers read slower than the phone really is")
    footer.summary = "measured (%s), saved to %s" % (args.label, path)
    footer.next = CMD + (" backup" if args.label == "before" else " report")


def cmd_report(args, footer):
    phone = pick_phone(footer)
    if not phone:
        return
    folder = bench_dir(phone.id)
    runs = sorted(f for f in os.listdir(folder) if f.endswith(".json")) if os.path.isdir(folder) else []
    before_path = args.before or next((os.path.join(folder, f) for f in runs if f.endswith("-before.json")), None)
    after_path = args.after or next((os.path.join(folder, f) for f in reversed(runs) if f.endswith("-after.json")),
                                    None)
    if not before_path or not after_path:
        footer.stop("need a 'before' and an 'after' benchmark", "%s bench --label %s" % (
            CMD, "before" if not before_path else "after"))
        return
    with open(before_path) as f:
        before = json.load(f)
    with open(after_path) as f:
        after = json.load(f)
    title = args.title or "%s %s" % (phone.getprop("ro.product.manufacturer"), phone.getprop("ro.product.model"))
    md, svg, png, lines = bench.write_report(before, after, "reports", title)
    for line in lines:
        print("  " + line)
    print("Report: %s\nCard:   %s" % (md, png or svg))
    footer.summary = "%d improvement(s): %s" % (len(lines), "; ".join(lines[:3]))


# ---------------------------------------------------------------- go (the one-prompt flow)

def run_step(title, fn, footer, **kwargs):
    """Run another step inside `go`; returns its footer, and copies a stop onto go's footer."""
    print("\n== %s" % title)
    sub = Footer()
    fn(argparse.Namespace(**kwargs), sub)
    if sub.result == "warn" and footer.result == "ok":
        footer.result = "warn"
    if sub.result == "stop":
        footer.stop(sub.summary, sub.next, sub.ask)
    return sub


def numbered_groups(path):
    groups = []
    for pkg, group, _ in kb.parse_list(path):
        if not groups or groups[-1][0] != group:
            groups.append((group, []))
        groups[-1][1].append(pkg)
    return groups


def keep_choices(path, keep):
    """Comment out the groups (by number) and packages the person keeps. Returns what stays listed."""
    groups = numbered_groups(path)
    tokens = [t for t in re.split(r"[\s,]+", (keep or "").strip().lower()) if t and t != "none"]
    kept = set()
    for t in tokens:
        if t.isdigit() and 1 <= int(t) <= len(groups):
            kept.update(groups[int(t) - 1][1])
        elif "." in t:
            kept.add(t)
    with open(path) as f:
        lines = f.read().splitlines()
    out = []
    for line in lines:
        pkg = line.split("#", 1)[0].strip()
        out.append("# keep: " + line if pkg and pkg in kept else line)
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    return [p for p in safety.read_list(path)], sorted(kept)


LETTERS = "abcdefghijklmnopqrstuvwxyz"


def rare_file(out):
    return out + ".rare.json"


def label(pkg):
    return usage.label(None, pkg)


def cmd_go(args, footer):
    if not args.yes:
        for title, fn, kw in (("Computer", cmd_doctor, {}), ("Phone connection", cmd_connect, {"pair": None, "wifi": None}),
                              ("Phone", cmd_probe, {})):
            if run_step(title, fn, footer, **kw).result == "stop":
                return
        phone = pick_phone(footer)
        print("\n== Measuring (before)")
        bench.save(bench.measure(phone, interactive=False), bench_dir(phone.id), "before")
        if run_step("Backup", cmd_backup, footer).result == "stop":
            return
        run_step("Looking for bloat", cmd_discover, footer, out=args.out)
        groups = numbered_groups(args.out) if os.path.exists(args.out) else []
        print("\n== Apps you rarely use")
        rare = usage.rarely_used(phone, safety.protected_now(phone), days=args.days)
        with open(rare_file(args.out), "w") as f:
            json.dump([pkg for pkg, _, _ in rare], f)
        if not groups and not rare:
            footer.summary = "no known bloat and no unused apps on this phone; speed tuning only"
            footer.next = CMD + " go --yes"
            return
        print("\nNothing has changed yet. Preinstalled bloat, by group:")
        for i, (group, pkgs) in enumerate(groups, 1):
            print("  %d. %s (%d): %s%s" % (i, group, len(pkgs), ", ".join(pkgs[:3]), ", ..." if len(pkgs) > 3 else ""))
        if rare:
            print("Apps you installed but haven't opened in %d+ days:" % args.days)
            for letter, (pkg, days, never) in zip(LETTERS, rare):
                print("  %s. %s  (%s)" % (letter, pkg, "never opened" if never else "%d days" % days))
        total = sum(len(p) for _, p in groups)
        parts = []
        if groups:
            parts.append("I found %d preinstalled apps you probably don't need, in %d groups: %s. Which group numbers "
                         "do you want to KEEP? (numbers like '2 5', or 'none' to remove them all)" % (
                             total, len(groups), "; ".join("%d. %s (%d)" % (i, g, len(p))
                                                           for i, (g, p) in enumerate(groups, 1))))
        if rare:
            parts.append(("These apps you installed haven't been opened in %d+ days: " % args.days) + "%s. Which letters should I "
                         "UNINSTALL? (letters like 'a c', or 'none'; the ones you keep get blocked from running in "
                         "the background)" % "; ".join("%s. %s" % (l, label(pkg)) for l, (pkg, _, _) in zip(LETTERS, rare)))
        parts.append("Everything can be undone with one command. Optional: say 'adblock' to also block ads "
                     "system-wide with AdGuard DNS (not YouTube ads; AdGuard sees which sites the phone looks up).")
        footer.summary = "%d bloat app(s) in %d group(s), %d rarely used app(s); nothing changed yet" % (
            total, len(groups), len(rare))
        footer.next = (CMD + " go --yes --keep <numbers or none> --uninstall <letters or none>"
                       "   (add --adblock only if they asked for it)")
        footer.ask = " ".join(parts)
        return

    phone = pick_phone(footer)
    if not phone:
        return
    if not journal.backups(phone.id):
        footer.stop("run '%s go' first (it backs up the phone)" % CMD, CMD + " go")
        return
    notes = []
    removed = 0
    if os.path.exists(args.out):
        todo, kept = keep_choices(args.out, args.keep)
        if kept:
            print("Keeping: " + ", ".join(kept))
        if todo:
            sub = run_step("Removing apps in batches", cmd_apply, footer, list=args.out, batch=5, yes=True, shots=False)
            if sub.result == "stop":
                return
            m = re.match(r"(\d+) disabled, (\d+) uninstalled for user, (\d+) suspended", sub.summary)
            removed = sum(int(x) for x in m.groups()) if m else 0

    log = journal.Journal(phone.id)
    rare = []
    if os.path.exists(rare_file(args.out)):
        with open(rare_file(args.out)) as f:
            rare = json.load(f)
    picked = {rare[LETTERS.index(t)] for t in re.split(r"[\s,]+", (args.uninstall or "").lower())
              if len(t) == 1 and t in LETTERS[:len(rare)]}
    if picked:
        print("\n== Uninstalling apps you picked")
        for pkg in sorted(picked):
            method = actions.remove(phone, pkg, log, run=journal.now(), methods=("uninstall_user",))
            print("  %-48s %s" % (pkg, "uninstalled" if method else "REFUSED, left as it was"))
            removed += 1 if method else 0
    restricted = 0
    for pkg in rare:
        if pkg not in picked and usage.restrict(phone, pkg, log):
            restricted += 1
    if restricted:
        print("\n== Background\n  %d rarely used app(s) blocked from running in the background" % restricted)

    print("\n== Speed")
    screen = ui.unlocked(phone)
    for ns, key, value, recipe, what in tune.speed_targets(phone, skin(phone)):
        result = actions.set_setting(phone, ns, key, value, log, ui_recipe=recipe, use_ui=screen)
        print("  %-28s %s" % (key, result))
        if result in ("refused", "ui_failed"):
            notes.append("couldn't change %s (%s)" % (what, tune.MANUAL[what]))
    print("  app caches cleared: about %d MB freed" % tune.trim_caches(phone))

    if args.adblock:
        print("\n== Ad blocking")
        host = tune.PROVIDERS["adguard"][0]
        r1 = actions.set_setting(phone, "global", "private_dns_specifier", host, log)
        r2 = actions.set_setting(phone, "global", "private_dns_mode", "hostname", log)
        if "refused" in (r1, r2):
            notes.append("ad blocking skipped: this phone only allows it by hand (%s)"
                         % tune.PRIVATE_DNS_MANUAL.format(host=host))
        else:
            blocked, online = tune.ads_blocked(phone)
            if not online:
                actions.set_setting(phone, "global", "private_dns_mode", "opportunistic", log)
                notes.append("ad blocking switched back off: websites stopped loading with it")
            print("  ads blocked: %s" % ("yes" if blocked else "not yet"))

    if run_step("Restart and check", cmd_verify, footer, reboot=True).result == "stop":
        return
    print("\n== Measuring (after)")
    folder = bench_dir(phone.id)
    after = bench.save(bench.measure(phone, interactive=False), folder, "after")
    befores = sorted(f for f in os.listdir(folder) if f.endswith("-before.json"))
    lines = []
    if befores:
        with open(os.path.join(folder, befores[-1])) as f:
            before = json.load(f)
        with open(after) as f:
            title = "%s %s" % (phone.getprop("ro.product.manufacturer"), phone.getprop("ro.product.model"))
            md, svg, png, lines = bench.write_report(before, json.load(f), "reports", title)
        print("  card: %s" % (png or svg))
    print("\nDONE")
    print("  removed: %d app(s)" % removed)
    if restricted:
        print("  blocked from the background: %d rarely used app(s)" % restricted)
    for line in lines:
        print("  " + line)
    for note in notes:
        print("  note: " + note)
    print("  undo everything: %s restore" % CMD)
    footer.summary = "done: %d app(s) removed%s" % (removed, "; " + "; ".join(lines[:3]) if lines else "")


# ---------------------------------------------------------------- lint (offline)

def cmd_lint(args, footer):
    bad = 0
    for path in args.lists:
        problems = safety.lint_list(path)
        for no, pkg, problem in problems:
            print("%s:%d: %s: %s" % (path, no, pkg, problem))
        bad += len(problems)
    if bad:
        footer.stop("%d problem(s) in %d list(s)" % (bad, len(args.lists)))
    else:
        footer.summary = "%d list(s) clean" % len(args.lists)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cmp", description="cleanyourphone: debloat, de-ad and speed up an "
                                     "Android phone over adb, in verified batches, with a restore.")
    parser.add_argument("--version", action="version", version="cleanyourphone " + VERSION)
    sub = parser.add_subparsers(dest="step", required=True)
    p = sub.add_parser("go", help="the whole job in two calls: read-only first (one question), then --yes")
    p.add_argument("--yes", action="store_true", help="apply: remove, tune, reboot-check, measure, report")
    p.add_argument("--keep", default="none", help="group numbers and/or package names to keep, e.g. '2 5'")
    p.add_argument("--uninstall", default="none", help="letters of rarely used apps to uninstall, e.g. 'a c'")
    p.add_argument("--adblock", action="store_true", help="also block ads system-wide with AdGuard DNS")
    p.add_argument("--days", type=int, default=30, help="an app unopened this many days counts as rarely used")
    p.add_argument("--out", default="my-list.local.txt", help=argparse.SUPPRESS)
    sub.add_parser("doctor", help="check Python, adb and the phone connection")
    p = sub.add_parser("connect", help="pick the phone (USB, or Wireless debugging)")
    p.add_argument("--pair", nargs=2, metavar=("HOST:PORT", "CODE"), help="pair with Wireless debugging first")
    p.add_argument("--wifi", metavar="HOST:PORT", help="connect over Wireless debugging")
    sub.add_parser("probe", help="model, Android version, RAM, storage, battery, warnings")
    sub.add_parser("backup", help="snapshot packages, settings and properties before any change")
    sub.add_parser("status", help="where you are: backups and journaled changes")
    p = sub.add_parser("discover", help="propose a debloat list for this phone from the known lists")
    p.add_argument("--out", default="my-list.local.txt", help="where to write the proposal")
    p = sub.add_parser("apply", help="remove the apps in a debloat list, in verified batches")
    p.add_argument("list")
    p.add_argument("--batch", type=int, default=5, help="apps per batch, 1-10 (default 5)")
    p.add_argument("--yes", action="store_true", help="actually do it (without it, apply only shows the plan)")
    p.add_argument("--shots", action="store_true", help="save a screenshot after each batch to shots/")
    p = sub.add_parser("verify", help="check the phone is healthy; --reboot checks it after a restart")
    p.add_argument("--reboot", action="store_true")
    p = sub.add_parser("restore", help="undo removals: everything, --last batch, or named packages")
    p.add_argument("packages", nargs="*")
    p.add_argument("--last", action="store_true", help="only the most recent batch")
    p = sub.add_parser("tune", help="speed: faster animations, highest refresh rate, clear caches")
    p.add_argument("--ui", action="store_true", help="tap through Settings where the phone blocks adb (screen on)")
    p.add_argument("--deep", action="store_true", help="also recompile apps and run storage maintenance (slow)")
    p = sub.add_parser("adblock", help="system-wide ad blocking with Private DNS, plus the phone's own ad switches")
    p.add_argument("--provider", choices=sorted(tune.PROVIDERS))
    p.add_argument("--check", action="store_true", help="check ads are blocked and the internet still works")
    p.add_argument("--off", action="store_true", help="switch the ad-blocking DNS off")
    p = sub.add_parser("bench", help="measure free RAM, processes, app start times and scrolling jank")
    p.add_argument("--label", default="before", choices=["before", "after"])
    p.add_argument("--quick", action="store_true", help="skip the app-start and scrolling tests (no screen needed)")
    p = sub.add_parser("report", help="before/after as approximate percentages, plus a shareable card")
    p.add_argument("--before")
    p.add_argument("--after")
    p.add_argument("--title")
    p = sub.add_parser("lint", help="check debloat lists offline (format and never-touch packages)")
    p.add_argument("lists", nargs="+")
    args = parser.parse_args(argv)

    footer = Footer()
    try:
        globals()["cmd_" + args.step](args, footer)
    except AdbError as e:
        footer.stop("adb error: %s" % e, CMD + " doctor")
    except KeyboardInterrupt:
        footer.stop("interrupted", CMD + " status")
    return footer.emit()


if __name__ == "__main__":
    sys.exit(main())
