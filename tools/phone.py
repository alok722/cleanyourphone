"""Thin wrapper around the stock `adb` binary.

Every phone command goes through Phone.shell(), one command per call: chained commands can stop
silently after the first on some firmware, and one call per command keeps each result checkable.
Set CYP_ADB to use another adb (the tests point it at tests/fake_adb.py).
"""
import hashlib
import os
import shutil
import subprocess
import sys
import time

ADB_CANDIDATES = [
    "~/Library/Android/sdk/platform-tools/adb",
    "/opt/homebrew/bin/adb",
    "/usr/local/bin/adb",
    "~/Android/Sdk/platform-tools/adb",
    "~/AppData/Local/Android/Sdk/platform-tools/adb.exe",
]


class AdbError(Exception):
    pass


def find_adb():
    """Path to adb, or None."""
    override = os.environ.get("CYP_ADB")
    if override:
        return override
    found = shutil.which("adb")
    if found:
        return found
    for cand in ADB_CANDIDATES:
        path = os.path.expanduser(cand)
        if os.path.isfile(path):
            return path
    return None


def _adb_argv(adb):
    # The fake adb used by the tests is a Python script; run it with this interpreter.
    if adb.endswith(".py"):
        return [sys.executable, "-I", adb]
    return [adb]


def run_adb(args, serial=None, timeout=60, adb=None):
    adb = adb or find_adb()
    if not adb:
        raise AdbError("adb not found")
    argv = _adb_argv(adb) + (["-s", serial] if serial else []) + list(args)
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AdbError("adb timed out: " + " ".join(args))
    out = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    err = proc.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
    return proc.returncode, out, err


def list_devices(adb=None):
    """[(serial, state, details)] where state is device, unauthorized, offline, ..."""
    rc, out, err = run_adb(["devices", "-l"], adb=adb)
    if rc != 0:
        raise AdbError(err.strip() or "adb devices failed")
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            details = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            devices.append((parts[0], parts[1], details))
    return devices


def device_id(serial):
    """A short stable id for a phone that does not reveal its serial number."""
    return hashlib.sha256(serial.encode()).hexdigest()[:10]


class Phone:
    def __init__(self, serial, adb=None):
        self.serial = serial
        self.adb = adb or find_adb()
        # The hardware serial, not the adb transport serial: over Wireless debugging the transport is
        # "ip:port" and changes on every reconnect, which would orphan the journal.
        hw = self.getprop("ro.serialno")
        self.id = device_id(hw if hw and "Exception" not in hw else serial)

    def adb_cmd(self, args, timeout=60):
        return run_adb(args, serial=self.serial, timeout=timeout, adb=self.adb)

    def shell(self, cmd, timeout=60):
        """Run one shell command. Returns (rc, combined output) with output stripped."""
        rc, out, err = self.adb_cmd(["shell", cmd], timeout=timeout)
        text = (out + err).strip()
        return rc, text

    def out(self, cmd, timeout=60):
        """Output of one shell command, ignoring the exit code (many pm/settings calls exit 0 on failure)."""
        return self.shell(cmd, timeout=timeout)[1]

    def getprop(self, name):
        return self.out("getprop " + name)

    def packages(self, flags=""):
        """Set of package names from `pm list packages <flags>`."""
        text = self.out(("pm list packages " + flags).strip())
        return {line[len("package:"):].strip() for line in text.splitlines() if line.startswith("package:")}

    def role_holders(self, role):
        text = self.out("cmd role get-role-holders " + role)
        if "Exception" not in text and "Unknown command" not in text and "Can't find service" not in text:
            return [p for p in text.replace(";", " ").split() if "." in p]
        # Before Android 10 there are no roles; ask the older APIs.
        if role.endswith(".SMS"):
            value = self.setting("secure", "sms_default_application")
            return [value] if value else []
        if role.endswith(".DIALER"):
            value = self.out("telecom get-default-dialer").strip()
            return [value] if "." in value and " " not in value else []
        if role.endswith(".HOME"):
            lines = self.out("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                             "-c android.intent.category.HOME").splitlines()
            comp = lines[-1].strip() if lines else ""
            return [comp.split("/", 1)[0]] if "/" in comp else []
        return []

    def setting(self, namespace, key):
        value = self.out("settings get %s %s" % (namespace, key))
        return None if value in ("", "null") else value

    def screenshot(self, path):
        """Save a PNG of the screen to path. Returns True if bytes came back."""
        # exec-out returns raw PNG bytes, so this bypasses run_adb's text decoding.
        argv = _adb_argv(self.adb) + ["-s", self.serial, "exec-out", "screencap", "-p"]
        data = subprocess.run(argv, capture_output=True, timeout=60).stdout
        if not data:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return True

    def booted(self):
        return self.getprop("sys.boot_completed") == "1"

    def reboot_and_wait(self, timeout=300):
        self.out("sync")
        self.adb_cmd(["reboot"], timeout=30)
        time.sleep(10)
        self.adb_cmd(["wait-for-device"], timeout=timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.booted():
                    time.sleep(10)  # let the launcher and services settle
                    return True
            except AdbError:
                pass
            time.sleep(3)
        return False
