"""Tapping through the phone's own Settings, for firmware that refuses `settings put` over adb.

realme UI and ColorOS refuse every settings write from the shell (SecurityException) unless a
developer toggle is on, and that toggle resets at reboot. The same change made by tapping Settings
works, so the steps that need it read the screen with uiautomator and tap by label. The labels are
English; on another language the steps fall back to telling the person where to tap.
"""
import re
import time


class UiError(Exception):
    pass


def unlocked(phone):
    """True when the screen is on and past the lock screen."""
    phone.out("input keyevent KEYCODE_WAKEUP")
    time.sleep(0.5)
    window = phone.out("dumpsys window")
    return "isKeyguardShowing=true" not in window and "mDreamingLockscreen=true" not in window


def keep_awake(phone):
    """Count as user activity so a short screen timeout (realme defaults to 30 s) doesn't lock the phone."""
    phone.out("input keyevent KEYCODE_WAKEUP")


def dump(phone):
    """[(text, x, y)] for every labelled element on screen."""
    xml = ""
    # --windows reads every window on screen. The plain dump reads the "active" window, which on realme
    # UI with another accessibility service on (Olauncher's double-tap-to-lock) is either missing ("null
    # root node") or the stale page underneath. Android 10 and older lack --windows, so fall back.
    for mode in ("--windows ", "--windows ", ""):
        keep_awake(phone)
        result = phone.out("uiautomator dump %s/sdcard/cyp-ui.xml" % mode)
        if "dumped to" in result:
            xml = phone.out("cat /sdcard/cyp-ui.xml")
            phone.out("rm /sdcard/cyp-ui.xml")
            break
        time.sleep(1)
    items = []
    for node in re.finditer(r"<node [^>]*>", xml):
        text = re.search(r' text="([^"]*)"', node.group())
        bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node.group())
        if text and text.group(1) and bounds:
            x1, y1, x2, y2 = map(int, bounds.groups())
            items.append((text.group(1).replace("&amp;", "&"), (x1 + x2) // 2, (y1 + y2) // 2))
    return items


def find(phone, pattern):
    """(x, y) of the first element whose text matches the regex, or None."""
    for text, x, y in dump(phone):
        if re.search(pattern, text, re.I):
            return x, y
    return None


def tap(phone, xy):
    phone.out("input tap %d %d" % xy)
    time.sleep(1.5)


def open_settings(phone, action):
    """Open a Settings screen fresh (Settings reuses its task, so stop it first)."""
    for pkg in ("com.android.settings", "com.oplus.wirelesssettings"):
        phone.out("am force-stop " + pkg)
    phone.out("am start -a " + action)
    time.sleep(2.5)


def screen_size(phone):
    """(width, height) in pixels as apps see it: `wm size` override first, then physical; 1080x2400 if unknown."""
    out = phone.out("wm size")
    m = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(r"Physical size:\s*(\d+)x(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (1080, 2400)


def swipe_cmd(size, up=False, ms=250):
    """A scroll swipe in the middle of this screen. Fixed pixels broke on 720x1600 phones (realme narzo 20):
    a swipe starting below the bottom edge does nothing, so the list never moved."""
    w, h = size
    y1, y2 = (int(h * 0.3), int(h * 0.8)) if up else (int(h * 0.75), int(h * 0.35))
    return "input swipe %d %d %d %d %d" % (w // 2, y1, w // 2, y2, ms)


def scroll_to(phone, pattern, swipes=20, up=False):
    """Scroll until an element matching pattern is on screen; its (x, y).

    A slow drag, not a fling: a fast swipe keeps the list coasting after the finger lifts and can carry
    a short row past the screen between two reads. A drag moves about 40% of the screen, so reads overlap.
    """
    swipe = swipe_cmd(screen_size(phone), up=up, ms=900)
    for _ in range(swipes):
        xy = find(phone, pattern)
        if xy:
            # A list still coasting from the last swipe eats the next tap, so let it settle and re-read.
            time.sleep(1.2)
            return find(phone, pattern) or xy
        phone.out(swipe)
        time.sleep(0.8)
    raise UiError("couldn't find '%s' on screen" % pattern)


def choose(phone, row_pattern, option_pattern, up=False):
    """Tap a settings row, then an option in the dialog or page it opens."""
    tap(phone, scroll_to(phone, row_pattern, up=up))
    xy = find(phone, option_pattern)
    if not xy:
        phone.out("input keyevent KEYCODE_BACK")
        raise UiError("couldn't find the option '%s'" % option_pattern)
    tap(phone, xy)


def home(phone):
    phone.out("input keyevent KEYCODE_HOME")
