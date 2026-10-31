"""Apps the owner installed but rarely opens, and keeping rarely used apps out of the background.

Last use comes from Android's own usage stats (`dumpsys usagestats`, yearly section), compared with
the phone's clock and each app's install time, so an app installed last week isn't "unused for a
year". Only apps with a launcher icon count: Google's background components that arrive through the
Play Store (contact keys, SafetyCore, the package verifier) are installed the same way but aren't apps.

Background: Android sorts apps into standby buckets. `am set-standby-bucket <pkg> restricted` puts
an app in the strictest one (no background jobs, alarms or network while unused); the old bucket is
journaled so `cmp restore` puts it back.
"""
import re
from datetime import datetime

FMT = "%Y-%m-%d %H:%M:%S"
BUCKETS = {10: "active", 20: "working_set", 30: "frequent", 40: "rare", 45: "restricted"}


def _time(text):
    try:
        t = datetime.strptime(text, FMT)
    except ValueError:
        return None
    return t if t.year > 1971 else None


def last_used(phone):
    """{package: datetime} from the yearly usage stats."""
    text = phone.out("dumpsys usagestats", timeout=120)
    start = text.find("In-memory yearly stats")
    used = {}
    for m in re.finditer(r'package=(\S+) totalTimeUsed="[^"]*" lastTimeUsed="([^"]+)"', text[start if start >= 0 else 0:]):
        t = _time(m.group(2))
        if t and (m.group(1) not in used or t > used[m.group(1)]):
            used[m.group(1)] = t
    return used


def launcher_apps(phone):
    out = phone.out("cmd package query-activities --brief -a android.intent.action.MAIN "
                    "-c android.intent.category.LAUNCHER")
    return {line.strip().split("/", 1)[0] for line in out.splitlines() if "/" in line}


def installed_at(phone, pkg):
    m = re.search(r"firstInstallTime=(\S+ \S+)", phone.out("dumpsys package %s | grep -m1 firstInstallTime" % pkg))
    return _time(m.group(1)) if m else None


def rarely_used(phone, protected, days=30):
    """[(package, days_unused, never_opened)] for owner-installed apps not opened in `days`, oldest first."""
    now = _time(phone.out('date "+%Y-%m-%d %H:%M:%S"')) or datetime.now()
    used = last_used(phone)
    apps = (phone.packages("-3 -e") & launcher_apps(phone)) - set(protected)
    found = []
    for pkg in apps:
        last = used.get(pkg)
        since = last or installed_at(phone, pkg)
        if since is None:
            continue
        idle = (now - since).days
        if idle >= days:
            found.append((pkg, idle, last is None))
    return sorted(found, key=lambda r: -r[1])


def label(phone, pkg):
    """A readable name: the package's last meaningful part (adb has no app-label query)."""
    parts = [p for p in pkg.split(".") if p not in ("com", "org", "in", "app", "android", "mobile", "customer")]
    return (parts[-1] if parts else pkg).replace("_", " ")


def bucket(phone, pkg):
    out = phone.out("am get-standby-bucket " + pkg)
    return int(out) if out.strip().isdigit() else None


def restrict(phone, pkg, log):
    """Put an app in the restricted standby bucket. True when the phone confirms it."""
    before = bucket(phone, pkg)
    if before is None or before >= 45:
        return False
    log.append(action="bucket", pkg=pkg, before=before, after=45, result="pending")
    phone.out("am set-standby-bucket %s restricted" % pkg)
    ok = bucket(phone, pkg) == 45
    log.append(action="bucket", pkg=pkg, before=before, after=45, result="ok" if ok else "refused")
    return ok


def unrestrict(phone, pkg, before, log):
    phone.out("am set-standby-bucket %s %s" % (pkg, BUCKETS.get(before, "rare")))
    ok = bucket(phone, pkg) != 45
    log.append(action="restore_bucket", pkg=pkg, before=before, result="ok" if ok else "failed")
    return ok


def active_restrictions(entries):
    """[(pkg, before)] restricted by cleanyourphone and not put back since."""
    current = {}
    for e in entries:
        if e.get("result") != "ok":
            continue
        if e.get("action") == "bucket":
            current.setdefault(e["pkg"], e["before"])
        elif e.get("action") == "restore_bucket":
            current.pop(e["pkg"], None)
    return list(current.items())
