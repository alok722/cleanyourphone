"""What each known package is, read from the device lists themselves.

Every `devices/*/debloat.txt` line sits under a `# heading` (its group) and may carry a trailing
`# note`. Together they are the knowledge base: `cmp discover` matches a phone's packages against
it, so each list contributed for one phone helps every other phone with the same packages.
A tested list wins over a starter list when both describe a package.
"""
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_list(path):
    """[(package, group, note)] from one debloat list."""
    rows, group = [], "Other"
    with open(path) as f:
        for line in f:
            text = line.rstrip("\n")
            stripped = text.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
                # A heading is a short comment line right before packages; long prose is header text.
                if heading and len(heading) < 90 and not heading.endswith((".", ":")):
                    group = heading
                continue
            if not stripped:
                continue
            pkg, _, note = stripped.partition("#")
            rows.append((pkg.strip(), group, note.strip()))
    return rows


def device_folders(root=ROOT):
    """[(folder, meta)] for every device list, tested ones first."""
    folders = []
    for meta_path in sorted(glob.glob(os.path.join(root, "devices", "*", "phone.json"))):
        with open(meta_path) as f:
            meta = json.load(f)
        folders.append((os.path.dirname(meta_path), meta))
    folders.sort(key=lambda fm: fm[1].get("status") != "tested")
    return folders


def load(root=ROOT):
    """{package: {"group", "note", "source", "status"}}"""
    kb = {}
    for folder, meta in device_folders(root):
        path = os.path.join(folder, "debloat.txt")
        if not os.path.exists(path):
            continue
        for pkg, group, note in parse_list(path):
            entry = kb.setdefault(pkg, {"group": group, "note": note, "source": os.path.basename(folder),
                                        "status": meta.get("status", "starter")})
            if not entry["note"] and note:
                entry["note"] = note
    return kb


def matching_device(model, root=ROOT):
    """(folder name, meta) of a list made for this exact model, else (None, None)."""
    model = (model or "").lower()
    for folder, meta in device_folders(root):
        if model and model in [m.lower() for m in meta.get("models", [])]:
            return os.path.basename(folder), meta
    return None, None
