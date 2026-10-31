"""Before/after measurements (`cmp bench`) and the shareable comparison (`cmp report`).

bench changes nothing except opening a few apps; it needs the screen unlocked for the app-start
and scrolling measurements. report only ever publishes approximate percentages, rounded to 5%
("~65% more free RAM"), never raw figures, so a card can be shared without exposing the phone.
"""
import html
import json
import os
import re
import statistics
import time

import ui

APPS = [("Settings", "com.android.settings"), ("Play Store", "com.android.vending")]


def launcher_component(phone, pkg):
    lines = phone.out("cmd package resolve-activity --brief -c android.intent.category.LAUNCHER " + pkg).splitlines()
    comp = lines[-1].strip() if lines else ""
    return comp if "/" in comp else None


def cold_start_ms(phone, component, runs=3):
    """Median TotalTime of `am start -W -S` over a few runs, or None."""
    times = []
    for _ in range(runs):
        ui.keep_awake(phone)
        out = phone.out("am start -W -S -n " + component, timeout=60)
        m = re.search(r"TotalTime:\s*(\d+)", out)
        if m:
            times.append(int(m.group(1)))
        ui.home(phone)
        time.sleep(1.5)
    return int(statistics.median(times)) if times else None


def scroll_jank(phone):
    """(janky %, frames) while scrolling the Settings list, or (None, None)."""
    pkg = "com.android.settings"
    phone.out("am force-stop " + pkg)
    phone.out("am start -W -n com.android.settings/.Settings")
    time.sleep(2)
    phone.out("dumpsys gfxinfo %s reset" % pkg)
    for i in range(8):
        if i % 2:
            phone.out("input swipe 540 700 540 1900 180")
        else:
            phone.out("input swipe 540 1900 540 700 180")
        time.sleep(0.4)
    out = phone.out("dumpsys gfxinfo " + pkg)
    ui.home(phone)
    frames = re.search(r"Total frames rendered:\s*(\d+)", out)
    janky = re.search(r"Janky frames:\s*\d+\s*\(([0-9.]+)%\)", out)
    if not frames or not janky:
        return None, None
    return float(janky.group(1)), int(frames.group(1))


def measure(phone, interactive=True):
    mem = {}
    for line in phone.out("cat /proc/meminfo").splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():
            mem[parts[0]] = int(parts[1])
    data = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": phone.getprop("ro.product.model"),
        "ram_available_mb": mem.get("MemAvailable", 0) // 1024,
        "swap_used_mb": (mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) // 1024,
        "processes": len(phone.out("ps -A -o NAME").splitlines()) - 1,
        "packages_installed": len(phone.packages()),
        "packages_enabled": len(phone.packages("-e")),
        "system_packages_enabled": len(phone.packages("-s -e")),
        "animation_scale": float(phone.setting("global", "window_animation_scale") or 1.0),
        "refresh_rate": float(phone.setting("system", "peak_refresh_rate") or 60.0),
        "private_dns": phone.setting("global", "private_dns_specifier")
        if phone.setting("global", "private_dns_mode") == "hostname" else None,
        "battery_saver": phone.setting("global", "low_power") == "1",
    }
    if interactive:
        starts = {}
        for name, pkg in APPS:
            comp = launcher_component(phone, pkg)
            if comp:
                starts[name] = cold_start_ms(phone, comp)
        data["cold_start_ms"] = starts
        data["janky_pct"], data["frames"] = scroll_jank(phone)
    return data


# ---------------------------------------------------------------- report

def approx(pct):
    """'~65%' style, rounded to 5; None when the change is too small to claim."""
    if pct is None or abs(pct) < 3:
        return None
    return "~%d%%" % max(5, int(round(abs(pct) / 5.0)) * 5)


def change(before, after):
    if before in (None, 0) or after is None:
        return None
    return (after - before) * 100.0 / before


def compare(before, after):
    """[(headline, detail)] in approximate terms only."""
    lines = []

    def add(pct, more, less):
        text = approx(pct)
        if text:
            lines.append(more % text if pct > 0 else less % text)

    add(change(before.get("ram_available_mb"), after.get("ram_available_mb")), "%s more free RAM", "%s less free RAM")
    apps = "packages_enabled" if before.get("packages_enabled") and after.get("packages_enabled") else "packages_installed"
    add(change(before.get(apps), after.get(apps)), "%s more apps", "%s fewer apps")
    add(change(before.get("processes"), after.get("processes")),
        "%s more background processes", "%s fewer background processes")
    b_starts, a_starts = before.get("cold_start_ms") or {}, after.get("cold_start_ms") or {}
    shared = [k for k in b_starts if b_starts.get(k) and a_starts.get(k)]
    if shared:
        add(change(sum(b_starts[k] for k in shared), sum(a_starts[k] for k in shared)),
            "apps open %s slower", "apps open %s faster")
    add(change(before.get("janky_pct"), after.get("janky_pct")), "%s more janky frames", "%s fewer janky frames")
    b_anim, a_anim = before.get("animation_scale"), after.get("animation_scale")
    if b_anim and a_anim and a_anim < b_anim:
        lines.append("~%g× faster animations" % round(b_anim / a_anim, 1))
    b_hz, a_hz = before.get("refresh_rate"), after.get("refresh_rate")
    if b_hz and a_hz and a_hz > b_hz:
        lines.append("%s smoother screen (%dHz)" % (approx(change(b_hz, a_hz)), a_hz))
    if after.get("private_dns") and not before.get("private_dns"):
        lines.append("ads blocked system-wide")
    return lines


def write_report(before, after, out_dir, title):
    os.makedirs(out_dir, exist_ok=True)
    lines = compare(before, after)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    md = os.path.join(out_dir, "report-%s.md" % stamp)
    with open(md, "w") as f:
        f.write("# %s\n\n" % title)
        for line in lines:
            f.write("- %s\n" % line)
        f.write("\nMeasured with cleanyourphone `cmp bench`, before and after. Approximate, rounded to 5%.\n")
    svg = os.path.join(out_dir, "card-%s.svg" % stamp)
    with open(svg, "w") as f:
        f.write(card_svg(title, lines))
    png = os.path.join(out_dir, "card-%s.png" % stamp)
    if not card_png(title, lines, png):
        png = None
    return md, svg, png, lines


def card_svg(title, lines):
    rows = "".join('<text x="80" y="%d" font-size="46" fill="#e8ecf4">%s</text>' % (330 + i * 86, html.escape(l))
                   for i, l in enumerate(lines[:7]))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080" viewBox="0 0 1080 1080">'
            '<rect width="1080" height="1080" fill="#0b0f17"/>'
            '<text x="80" y="150" font-size="34" fill="#7f8aa3" font-family="sans-serif">cleanyourphone</text>'
            '<text x="80" y="230" font-size="58" font-weight="700" fill="#ffffff" font-family="sans-serif">%s</text>'
            '<g font-family="sans-serif">%s</g>'
            '<text x="80" y="1000" font-size="30" fill="#7f8aa3" font-family="sans-serif">'
            'No root · every step reversible · github.com/alok722/cleanyourphone</text></svg>'
            % (html.escape(title), rows))


def card_png(title, lines, path):
    """A 1080x1080 PNG of the card, when Pillow is installed (optional)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False

    def font(size, bold=False):
        for name in (("Arial Bold.ttf", "DejaVuSans-Bold.ttf") if bold else ("Arial.ttf", "DejaVuSans.ttf")):
            for folder in ("/System/Library/Fonts/Supplemental", "/Library/Fonts", "/usr/share/fonts/truetype/dejavu",
                           "C:/Windows/Fonts"):
                try:
                    return ImageFont.truetype(os.path.join(folder, name), size)
                except OSError:
                    continue
        return ImageFont.load_default()

    img = Image.new("RGB", (1080, 1080), (11, 15, 23))
    d = ImageDraw.Draw(img)
    d.text((80, 118), "cleanyourphone", font=font(34), fill=(127, 138, 163))
    d.text((80, 182), title, font=font(58, True), fill=(255, 255, 255))
    for i, line in enumerate(lines[:7]):
        d.text((80, 292 + i * 86), "•  " + line, font=font(46), fill=(232, 236, 244))
    d.text((80, 972), "No root · every step reversible · github.com/alok722/cleanyourphone", font=font(30),
           fill=(127, 138, 163))
    img.save(path)
    return True


def save(data, folder, label):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s-%s.json" % (time.strftime("%Y%m%d-%H%M%S"), label))
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    return path
