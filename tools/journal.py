"""Per-phone state on the computer: backups and the change journal.

Layout under ~/.cleanyourphone/ (CYP_HOME overrides it):
    current                    serial of the phone the last `cmp connect` picked
    <device-id>/backups/<ts>/  snapshots written by `cmp backup`
    <device-id>/journal.jsonl  one line per change, written BEFORE the change is made
    <device-id>/state.json     progress: last verify, batches done

The journal is the only thing `cmp restore` trusts: every entry records what the package or
setting looked like before, so the undo never guesses.
"""
import json
import os
import time


def home():
    return os.path.expanduser(os.environ.get("CYP_HOME", "~/.cleanyourphone"))


def now():
    return time.strftime("%Y%m%d-%H%M%S")


def device_dir(device_id):
    path = os.path.join(home(), device_id)
    os.makedirs(path, exist_ok=True)
    return path


def save_current(serial):
    os.makedirs(home(), exist_ok=True)
    with open(os.path.join(home(), "current"), "w", encoding="utf-8") as f:
        f.write(serial)


def load_current():
    try:
        with open(os.path.join(home(), "current"), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def backups(device_id):
    path = os.path.join(device_dir(device_id), "backups")
    if not os.path.isdir(path):
        return []
    return sorted(os.path.join(path, d) for d in os.listdir(path))


def new_backup_dir(device_id):
    path = os.path.join(device_dir(device_id), "backups", now())
    os.makedirs(path, exist_ok=True)
    return path


class Journal:
    def __init__(self, device_id):
        self.path = os.path.join(device_dir(device_id), "journal.jsonl")

    def append(self, **entry):
        entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    def entries(self):
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    pass  # a line cut short by a crash; the lines around it still count
        return entries


class State:
    def __init__(self, device_id):
        self.path = os.path.join(device_dir(device_id), "state.json")

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def update(self, **values):
        data = self.load()
        data.update(values)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        return data
