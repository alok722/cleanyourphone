"""Health checks run before the first batch, after every batch, and after a reboot.

Each check returns (name, ok, detail). A batch fails only on a regression: a check that was
already failing in the baseline (no SIM, say) is reported but does not roll anything back.
"""
import time

CRITICAL_PROCESSES = ("com.android.systemui", "com.android.phone", "system_server")


def phone_time(phone):
    return phone.out('date "+%m-%d %H:%M:%S.000"')


def run(phone, since=None):
    results = []

    results.append(("boot completed", phone.booted(), ""))

    pid = phone.out("pidof com.android.systemui")
    results.append(("system UI running", bool(pid) and pid.split()[0].isdigit(), ""))

    home = phone.out("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                     "-c android.intent.category.HOME").splitlines()
    component = home[-1].strip() if home else ""
    results.append(("home screen resolves", "/" in component, component))

    ime = phone.setting("secure", "default_input_method") or ""
    ime_pkg = ime.split("/", 1)[0]
    results.append(("keyboard installed", bool(ime_pkg) and ime_pkg in phone.packages(ime_pkg), ime_pkg))

    for role, label in (("android.app.role.DIALER", "phone app"), ("android.app.role.SMS", "SMS app")):
        holders = phone.role_holders(role)
        results.append((label + " set", bool(holders), ",".join(holders)))

    results.append(("Play Store enabled", "com.android.vending" in phone.packages("-e com.android.vending"), ""))

    sim = phone.getprop("gsm.sim.state")
    results.append(("SIM loaded", "LOADED" in sim or "READY" in sim, sim))

    if since:
        crashes = phone.out('logcat -b crash -d -T "%s"' % since)
        crashed = sorted({line.split("Process: ", 1)[1].split(",")[0].strip()
                          for line in crashes.splitlines() if "Process: " in line})
        critical = [p for p in crashed if p in CRITICAL_PROCESSES]
        results.append(("no system crashes", not critical, ", ".join(crashed)))
    return results


def regressions(baseline, current):
    """Checks that passed in the baseline and fail now."""
    base_ok = {name for name, ok, _ in baseline if ok}
    return [(name, detail) for name, ok, detail in current if not ok and (name in base_ok or name == "no system crashes")]


def report(results):
    for name, ok, detail in results:
        print("  %s %s%s" % ("✓" if ok else "✗", name, "  (%s)" % detail if detail else ""))


def settle(seconds=3):
    """Give the phone a moment after a batch before checking it."""
    time.sleep(seconds)
