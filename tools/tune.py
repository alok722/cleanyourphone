"""Speed settings (`cmp tune`) and ad blocking (`cmp adblock`).

Every change goes through actions.set_setting, so it is journaled with the phone's own value first
and `cmp restore` can put it back. Where the firmware refuses `settings put`, a recipe taps through
Settings instead (only with --ui, screen unlocked), and failing that the person is told where to tap.
"""
import json
import os
import re

import actions
import ui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- speed

def _dev_option(label):
    def recipe(phone):
        ui.open_settings(phone, "android.settings.APPLICATION_DEVELOPMENT_SETTINGS")
        ui.choose(phone, "^%s$" % label, r"(^|\s)0?\.5x$")
        ui.home(phone)
    return recipe


def _colorful_refresh(phone):
    """realme UI / ColorOS: Display & brightness > Screen refresh rate > High."""
    ui.open_settings(phone, "android.settings.DISPLAY_SETTINGS")
    ui.choose(phone, "^Screen refresh rate$", "^High$")
    ui.home(phone)


ANIMATIONS = [
    ("window_animation_scale", "Window animation scale"),
    ("transition_animation_scale", "Transition animation scale"),
    ("animator_duration_scale", "Animator duration scale"),
]

MANUAL = {
    "animations": "Settings > About phone > tap Build number 7 times > Developer options > set Window animation "
                  "scale, Transition animation scale and Animator duration scale to 0.5x",
    "refresh": "Settings > Display > Refresh rate (or Smooth display / Motion smoothness) > the highest option",
}


def max_refresh(phone):
    rates = [float(r) for r in re.findall(r"fps=([0-9.]+)", phone.out("dumpsys display"))]
    return max(rates) if rates else None


def speed_targets(phone, skin):
    """[(ns, key, value, recipe, what)] the tune step aims for on this phone."""
    targets = [("global", key, "0.5", _dev_option(label), "animations") for key, label in ANIMATIONS]
    top = max_refresh(phone)
    if top and top > 60:
        recipe = _colorful_refresh if skin.startswith(("realme UI", "ColorOS", "OxygenOS")) else None
        targets.append(("system", "peak_refresh_rate", "%.1f" % top, recipe, "refresh"))
    return targets


def free_storage_kb(phone):
    for line in phone.out("df -k /data").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[3].isdigit():
            return int(parts[3])
    return 0


def trim_caches(phone):
    """Clear every app's cache. Returns MB freed."""
    before = free_storage_kb(phone)
    phone.out("pm trim-caches 999G", timeout=300)
    return max(0, free_storage_kb(phone) - before) // 1024


def deep_maintenance(phone):
    """Recompile apps from their usage profiles and run storage maintenance (fstrim). Slow."""
    phone.out("cmd package bg-dexopt-job", timeout=1800)
    phone.out("sm idle-maint run", timeout=600)


# ---------------------------------------------------------------- ad blocking

PROVIDERS = {
    "adguard": ("dns.adguard-dns.com", "AdGuard DNS: ads and trackers"),
    "adguard-family": ("family.adguard-dns.com", "AdGuard DNS Family: ads, trackers and adult sites"),
    "controld": ("p2.freedns.controld.com", "Control D free: ads and trackers"),
    "mullvad": ("adblock.dns.mullvad.net", "Mullvad DNS: ads and trackers, no logging"),
}

PRIVATE_DNS_MANUAL = ("Open Settings, search for 'Private DNS', choose 'Private DNS provider hostname' (or "
                      "'Designated'), type {host} and save")


def oem_ad_switches(skin):
    with open(os.path.join(ROOT, "data", "oem-ad-switches.json")) as f:
        switches = json.load(f)
    steps = []
    for prefix, items in switches.items():
        if prefix == "any" or skin.startswith(prefix):
            steps.extend(items)
    return steps


def resolves_to(phone, host):
    """The IP a host resolves to on the phone (through Private DNS), '' if it doesn't resolve."""
    out = phone.out("ping -c 1 -W 3 " + host, timeout=20)
    m = re.search(r"PING \S+ \(([0-9a-f.:]+)\)", out)
    return m.group(1) if m else ""


def ads_blocked(phone):
    """(blocked, still_online): an ad domain no longer resolves, a normal one still does."""
    ad = resolves_to(phone, "doubleclick.net")
    normal = resolves_to(phone, "wikipedia.org")
    return ad in ("", "0.0.0.0", "::"), normal not in ("", "0.0.0.0", "::")
