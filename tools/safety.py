"""What cleanyourphone refuses to touch.

Two layers, both enforced in code so no prompt wording can talk an assistant past them:
- data/never.txt: core packages, never disabled, uninstalled or suspended. No override.
- the phone's own role holders (Home, keyboard, SMS, dialer): skipped while they hold the role,
  so a list can never leave the phone without a launcher, a keyboard, or a way to call and text.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEVER_FILE = os.path.join(ROOT, "data", "never.txt")

GUARDED_ROLES = {
    "android.app.role.HOME": "home screen",
    "android.app.role.SMS": "SMS app",
    "android.app.role.DIALER": "phone app",
}


def load_never(path=NEVER_FILE):
    exact, prefixes = set(), []
    with open(path, encoding="utf-8") as f:
        for line in f:
            entry = line.split("#", 1)[0].strip()
            if not entry:
                continue
            if entry.endswith("*"):
                prefixes.append(entry[:-1])
            else:
                exact.add(entry)
    return exact, prefixes


def never_reason(pkg, never=None):
    """Why pkg is on the never list, or None."""
    exact, prefixes = never or load_never()
    if pkg in exact:
        return "core package (data/never.txt)"
    for prefix in prefixes:
        if pkg.startswith(prefix):
            return "core package (data/never.txt: %s*)" % prefix
    return None


def read_list(path):
    """Package names from a debloat list: one per line, # comments allowed anywhere."""
    pkgs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            entry = line.split("#", 1)[0].strip()
            if entry:
                pkgs.append(entry)
    return pkgs


def lint_list(path, never=None):
    """[(line_no, package, problem)] for a debloat list; empty means it is safe to apply."""
    never = never or load_never()
    problems, seen = [], set()
    with open(path, encoding="utf-8") as f:
        for no, line in enumerate(f, 1):
            entry = line.split("#", 1)[0].strip()
            if not entry:
                continue
            if " " in entry or not all(part for part in entry.split(".")) or "." not in entry:
                problems.append((no, entry, "not a package name"))
            elif entry in seen:
                problems.append((no, entry, "listed twice"))
            else:
                reason = never_reason(entry, never)
                if reason:
                    problems.append((no, entry, reason))
            seen.add(entry)
    return problems


def protected_now(phone):
    """{package: why} for what the phone currently relies on: role holders and the keyboard."""
    protected = {}
    for role, label in GUARDED_ROLES.items():
        for pkg in phone.role_holders(role):
            protected[pkg] = "current " + label
    ime = phone.setting("secure", "default_input_method")
    if ime and "/" in ime:
        protected[ime.split("/", 1)[0]] = "current keyboard"
    return protected
