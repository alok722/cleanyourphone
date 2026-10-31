import contextlib
import glob
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import cmp  # noqa: E402
import bench  # noqa: E402
import journal  # noqa: E402
import kb  # noqa: E402
import safety  # noqa: E402

SERIAL = "FAKESERIAL0RMX21"


class FakePhoneTest(unittest.TestCase):
    """Runs cmp steps against tests/fake_adb.py with a throwaway ~/.cleanyourphone."""

    fixture = "realme7"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {
            "CYP_ADB": os.path.join(ROOT, "tests", "fake_adb.py"),
            "CYP_HOME": self.tmp.name,
            "CYP_FAKE_PHONE": self.fixture,
            "CYP_FAKE_LOG": os.path.join(self.tmp.name, "adb.log"),
            "CYP_FAKE_STATE": os.path.join(self.tmp.name, "phone-state.json"),
        }
        self.saved = {k: os.environ.get(k) for k in list(self.env) + ["CYP_SERIAL"]}
        os.environ.update(self.env)
        os.environ.pop("CYP_SERIAL", None)
        self.settle, cmp.checks.settle = cmp.checks.settle, lambda seconds=0: None
        import phone as phone_mod
        self.sleeps = (cmp.time.sleep, phone_mod.time.sleep)
        cmp.time.sleep = phone_mod.time.sleep = lambda seconds: None  # reboot waits, in tests only

    def tearDown(self):
        cmp.checks.settle = self.settle
        import phone as phone_mod
        cmp.time.sleep, phone_mod.time.sleep = self.sleeps
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_cmp(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cmp.main(list(argv))
        return code, buf.getvalue()

    def footer(self, output):
        return {line.split(": ", 1)[0]: line.split(": ", 1)[1]
                for line in output.splitlines() if line.split(":", 1)[0] in ("RESULT", "SUMMARY", "NEXT", "ASK_USER")}

    def adb_calls(self):
        with open(self.env["CYP_FAKE_LOG"]) as f:
            return [json.loads(line) for line in f]


class TestRealme7(FakePhoneTest):
    def test_doctor_ready(self):
        code, out = self.run_cmp("doctor")
        f = self.footer(out)
        self.assertEqual((code, f["RESULT"]), (0, "ok"))
        self.assertIn("connect", f["NEXT"])
        self.assertNotIn(SERIAL, out, "doctor output may be pasted publicly: no full serial")

    def test_connect_remembers_phone(self):
        code, out = self.run_cmp("connect")
        self.assertEqual(code, 0)
        self.assertEqual(journal.load_current(), SERIAL)
        self.assertIn("probe", self.footer(out)["NEXT"])

    def test_probe_reads_phone_and_warns(self):
        code, out = self.run_cmp("probe")
        f = self.footer(out)
        self.assertEqual(f["RESULT"], "warn")
        self.assertIn("realme RMX2151, Android 12, realme UI V3.0", f["SUMMARY"])
        self.assertIn("Battery Saver is on", out)
        self.assertIn("battery at 8%", out)
        self.assertIn("not restarted for 12 days", out)
        self.assertIn("backup", f["NEXT"])

    def test_backup_writes_snapshot_without_serial(self):
        code, out = self.run_cmp("backup")
        self.assertEqual(code, 0)
        device = journal.device_dir(cmp.Phone(SERIAL).id)
        [snap] = journal.backups(os.path.basename(device))
        for name in ("packages-all.txt", "packages-disabled.txt", "settings-secure.txt", "getprop.txt", "meta.json"):
            self.assertTrue(os.path.getsize(os.path.join(snap, name)) > 0, name)
        with open(os.path.join(snap, "meta.json")) as f:
            meta = json.load(f)
        self.assertEqual(meta["roles"]["android.app.role.HOME"], ["app.olauncher"])
        self.assertNotIn(SERIAL, json.dumps(meta))
        self.assertIn("22 packages", out)

    def test_status_before_and_after_backup(self):
        self.assertIn("backup", self.footer(self.run_cmp("status")[1])["NEXT"])
        self.run_cmp("backup")
        self.assertIn("1 backup(s)", self.footer(self.run_cmp("status")[1])["SUMMARY"])

    def test_every_shell_call_is_one_command(self):
        self.run_cmp("backup")
        for call in self.adb_calls():
            if call[:1] == ["shell"]:
                self.assertEqual(len(call), 2)
                self.assertNotIn("&&", call[1])
                self.assertNotIn(";", call[1])

    def test_protected_now_covers_roles_and_keyboard(self):
        protected = safety.protected_now(cmp.Phone(SERIAL))
        self.assertEqual(protected["app.olauncher"], "current home screen")
        self.assertEqual(protected["com.google.android.apps.messaging"], "current SMS app")
        self.assertEqual(protected["com.google.android.dialer"], "current phone app")
        self.assertEqual(protected["com.google.android.inputmethod.latin"], "current keyboard")


class TestApplyRestore(FakePhoneTest):
    """Batch debloat, the method chain, rollback and restore, against the stateful fake realme 7."""

    def setUp(self):
        super().setUp()
        self.run_cmp("backup")

    def write_list(self, *pkgs):
        path = os.path.join(self.tmp.name, "list.txt")
        with open(path, "w") as f:
            f.write("# test list\n" + "\n".join(pkgs) + "\n")
        return path

    def phone_state(self):
        with open(self.env["CYP_FAKE_STATE"]) as f:
            return {k: (v if k in ("settings", "buckets") else set(v)) for k, v in json.load(f).items()}

    def save_phone_state(self, state):
        with open(self.env["CYP_FAKE_STATE"], "w") as f:
            json.dump({k: (v if k in ("settings", "buckets") else sorted(v)) for k, v in state.items()}, f)

    def entries(self):
        return journal.Journal(cmp.Phone(SERIAL).id).entries()

    def test_dry_run_asks_and_changes_nothing(self):
        path = self.write_list("com.heytap.browser", "com.coloros.weather2")
        code, out = self.run_cmp("apply", path)
        f = self.footer(out)
        self.assertIn("Go ahead?", f["ASK_USER"])
        self.assertTrue(f["NEXT"].endswith("--yes"))
        self.assertEqual(self.entries(), [])

    def test_apply_uses_method_chain_and_skips_protected(self):
        path = self.write_list("com.heytap.browser", "com.coloros.weather2", "com.coloros.compass2",
                               "com.heytap.market", "com.google.android.inputmethod.latin", "app.olauncher",
                               "com.glance.internet", "com.not.on.this.phone")
        code, out = self.run_cmp("apply", path, "--yes", "--batch", "2")
        f = self.footer(out)
        self.assertEqual(code, 0, out)
        self.assertEqual(f["SUMMARY"], "2 disabled, 1 uninstalled for user, 1 suspended, 0 refused, 4 skipped")
        self.assertIn("current keyboard, kept", out)
        self.assertIn("current home screen, kept", out)
        self.assertIn("not on this phone", out)
        state = self.phone_state()
        self.assertEqual(state["disabled"] - {"com.glance.internet"}, {"com.heytap.browser", "com.coloros.weather2"})
        self.assertEqual(state["uninstalled"], {"com.coloros.compass2"})
        self.assertEqual(state["suspended"], {"com.heytap.market"})
        market = [(e["method"], e["result"]) for e in self.entries() if e["pkg"] == "com.heytap.market"]
        self.assertEqual(market, [("disable", "pending"), ("disable", "refused"), ("uninstall_user", "pending"),
                                  ("uninstall_user", "refused"), ("suspend", "pending"), ("suspend", "ok")])

    def test_a_batch_that_breaks_home_is_rolled_back(self):
        path = self.write_list("com.heytap.browser", "com.fake.loadbearing", "com.coloros.weather2")
        code, out = self.run_cmp("apply", path, "--yes", "--batch", "2")
        f = self.footer(out)
        self.assertEqual((code, f["RESULT"]), (2, "stop"))
        self.assertIn("home screen resolves", f["SUMMARY"])
        state = self.phone_state()
        self.assertNotIn("com.fake.loadbearing", state["disabled"])
        self.assertNotIn("com.heytap.browser", state["disabled"])
        self.assertNotIn("com.coloros.weather2", state["disabled"], "batch 2 must never run")

    def test_restore_everything(self):
        path = self.write_list("com.heytap.browser", "com.coloros.compass2", "com.heytap.market")
        self.run_cmp("apply", path, "--yes")
        code, out = self.run_cmp("restore")
        self.assertEqual(self.footer(out)["SUMMARY"], "3 change(s) restored")
        state = self.phone_state()
        self.assertEqual(state["uninstalled"] | state["suspended"], set())
        self.assertEqual(state["disabled"], {"com.glance.internet"}, "restore must not touch what was off before")

    def test_restore_last_batch_only(self):
        path = self.write_list("com.heytap.browser", "com.coloros.weather2", "com.coloros.compass2")
        self.run_cmp("apply", path, "--yes", "--batch", "2")
        code, out = self.run_cmp("restore", "--last")
        self.assertEqual(self.footer(out)["SUMMARY"], "1 change(s) restored")
        self.assertEqual(self.phone_state()["uninstalled"], set())
        self.assertIn("com.heytap.browser", self.phone_state()["disabled"])

    def test_restore_named_package_not_removed_by_us(self):
        code, out = self.run_cmp("restore", "com.glance.internet")
        self.assertIn("not removed by cleanyourphone", out)
        self.assertIn("com.glance.internet", self.phone_state()["disabled"])

    def test_verify_reports_packages_that_came_back(self):
        path = self.write_list("com.heytap.browser", "com.coloros.weather2")
        self.run_cmp("apply", path, "--yes")
        state = self.phone_state()
        state["disabled"].discard("com.coloros.weather2")
        self.save_phone_state(state)
        code, out = self.run_cmp("verify")
        self.assertEqual(self.footer(out)["RESULT"], "warn")
        self.assertIn("came back on their own", out)
        self.assertIn("com.coloros.weather2", out)

    def test_apply_needs_a_backup(self):
        import shutil
        shutil.rmtree(os.path.join(self.tmp.name, cmp.Phone(SERIAL).id, "backups"))
        code, out = self.run_cmp("apply", self.write_list("com.heytap.browser"), "--yes")
        self.assertEqual(code, 2)
        self.assertIn("backup", self.footer(out)["NEXT"])

    def test_apply_refuses_a_list_with_a_core_package(self):
        code, out = self.run_cmp("apply", self.write_list("com.heytap.browser", "com.android.phone"), "--yes")
        self.assertEqual(code, 2)
        self.assertEqual(self.entries(), [])


class TestDiscover(FakePhoneTest):
    def test_discover_proposes_known_bloat_and_keeps_essentials(self):
        out_path = os.path.join(self.tmp.name, "proposal.txt")
        code, out = self.run_cmp("discover", "--out", out_path)
        f = self.footer(out)
        self.assertEqual(code, 0, out)
        self.assertIn("Which groups or apps do you want to keep?", f["ASK_USER"])
        self.assertTrue(f["NEXT"].endswith("apply " + out_path))
        self.assertIn("This exact model has a tested list: devices/realme-7-rmx2151", out)
        proposed = safety.read_list(out_path)
        for pkg in ("com.heytap.browser", "com.coloros.weather2", "com.heytap.market", "com.heytap.themestore"):
            self.assertIn(pkg, proposed)
        self.assertNotIn("com.glance.internet", proposed, "already disabled")
        self.assertNotIn("com.google.android.inputmethod.latin", proposed)
        self.assertNotIn("com.linkedin.android", proposed, "installed by the owner, not preinstalled")
        self.assertNotIn("android", proposed)
        self.assertEqual(safety.lint_list(out_path), [])
        self.assertEqual(journal.Journal(cmp.Phone(SERIAL).id).entries(), [], "discover changes nothing")

    def test_discover_then_apply_round_trip(self):
        out_path = os.path.join(self.tmp.name, "proposal.txt")
        self.run_cmp("backup")
        self.run_cmp("discover", "--out", out_path)
        code, out = self.run_cmp("apply", out_path, "--yes")
        self.assertEqual(code, 0, out)
        code, out = self.run_cmp("discover", "--out", out_path)
        self.assertIn("no known bloat left", self.footer(out)["SUMMARY"])


class TestKnowledgeBase(unittest.TestCase):
    def test_groups_and_notes_come_from_the_lists(self):
        known = kb.load()
        self.assertEqual(known["com.glance.internet"]["group"], "Ads & tracking")
        self.assertEqual(known["com.glance.internet"]["status"], "tested", "tested list wins over starters")
        self.assertEqual(known["com.heytap.themestore"]["group"], "Theme Store (needs suspend on this build, see the quirks above)")
        self.assertEqual(known["com.miui.msa.global"]["status"], "starter")
        self.assertIn("ad service", known["com.miui.msa.global"]["note"])

    def test_matching_device_by_model(self):
        self.assertEqual(kb.matching_device("RMX2151")[0], "realme-7-rmx2151")
        self.assertEqual(kb.matching_device("SM-A505F"), (None, None))

    def test_starter_lists_say_so(self):
        for folder, meta in kb.device_folders():
            if meta["status"] == "starter":
                with open(os.path.join(folder, "debloat.txt")) as f:
                    self.assertIn("STARTER LIST, UNTESTED", f.read(), folder)


class TestTuneAdblockBench(TestApplyRestore):
    """Speed settings, Private DNS, benchmark and report against the fake realme 7."""

    def settings(self):
        return self.phone_state()["settings"]

    def tearDown(self):
        for k in ("CYP_FAKE_REFUSE_SETTINGS", "CYP_FAKE_LOCKED"):
            os.environ.pop(k, None)
        super().tearDown()

    def test_tune_sets_animations_and_top_refresh_rate(self):
        code, out = self.run_cmp("tune")
        self.assertEqual(code, 0, out)
        st = self.settings()
        self.assertEqual(st["global/window_animation_scale"], "0.5")
        self.assertEqual(st["global/animator_duration_scale"], "0.5")
        self.assertEqual(st["system/peak_refresh_rate"], "90.0", "highest mode the panel reports")
        self.assertIn("Battery Saver is on", out)
        self.assertIn("adblock", self.footer(out)["NEXT"])

    def test_tune_on_firmware_that_refuses_settings_asks_to_unlock(self):
        os.environ["CYP_FAKE_REFUSE_SETTINGS"] = "1"
        code, out = self.run_cmp("tune")
        f = self.footer(out)
        self.assertEqual(f["NEXT"], "./cmp tune --ui")
        self.assertIn("Unlock the phone", f["ASK_USER"])
        self.assertEqual(self.settings()["global/window_animation_scale"], "1.0")
        os.environ["CYP_FAKE_LOCKED"] = "1"
        code, out = self.run_cmp("tune", "--ui")
        self.assertEqual(code, 2)
        self.assertIn("locked", self.footer(out)["SUMMARY"])

    def test_restore_puts_settings_back(self):
        self.run_cmp("tune")
        code, out = self.run_cmp("restore")
        st = self.settings()
        self.assertEqual(st["global/window_animation_scale"], "1.0")
        self.assertEqual(st["system/peak_refresh_rate"], "60.0")
        self.assertNotIn("global/animator_duration_scale", st, "it was unset before, so it is deleted again")

    def test_adblock_asks_then_sets_and_checks(self):
        code, out = self.run_cmp("adblock")
        f = self.footer(out)
        self.assertIn("YouTube", f["ASK_USER"])
        self.assertIn("Delete advertising ID", out)
        self.assertNotIn("global/private_dns_mode", self.settings(), "asking changes nothing")
        self.run_cmp("adblock", "--provider", "adguard")
        st = self.settings()
        self.assertEqual((st["global/private_dns_mode"], st["global/private_dns_specifier"]),
                         ("hostname", "dns.adguard-dns.com"))
        code, out = self.run_cmp("adblock", "--check")
        self.assertEqual(self.footer(out)["RESULT"], "ok", out)
        self.assertIn("ad domain blocked:  yes", out)

    def test_adblock_when_refused_gives_manual_steps(self):
        os.environ["CYP_FAKE_REFUSE_SETTINGS"] = "1"
        code, out = self.run_cmp("adblock", "--provider", "adguard")
        f = self.footer(out)
        self.assertIn("dns.adguard-dns.com", f["ASK_USER"])
        self.assertIn("--check", f["NEXT"])
        code, out = self.run_cmp("adblock", "--check")
        self.assertEqual(self.footer(out)["RESULT"], "stop")

    def test_bench_quick_and_report(self):
        code, out = self.run_cmp("bench", "--quick", "--label", "before")
        self.assertEqual(code, 0, out)
        self.run_cmp("tune")
        self.run_cmp("adblock", "--provider", "adguard")
        self.run_cmp("apply", self.write_list("com.heytap.browser", "com.coloros.weather2"), "--yes")
        self.run_cmp("bench", "--quick", "--label", "after")
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            code, out = self.run_cmp("report", "--title", "realme 7")
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, out)
        self.assertIn("~2× faster animations", out)
        self.assertIn("~50% smoother screen (90Hz)", out)
        self.assertIn("ads blocked system-wide", out)
        self.assertIn("~10% fewer apps", out)
        self.assertTrue(glob.glob(os.path.join(self.tmp.name, "reports", "card-*.svg")))

    def test_bench_needs_unlocked_screen_unless_quick(self):
        os.environ["CYP_FAKE_LOCKED"] = "1"
        code, out = self.run_cmp("bench")
        self.assertEqual(code, 2)
        self.assertIn("unlocked", self.footer(out)["ASK_USER"])


class TestGo(FakePhoneTest):
    """The one-prompt flow: `go` asks one question, `go --yes --keep ...` does the rest."""

    def test_first_call_is_read_only_and_asks_one_question(self):
        out_path = os.path.join(self.tmp.name, "go.txt")
        code, out = self.run_cmp("go", "--out", out_path)
        f = self.footer(out)
        self.assertEqual(code, 0, out)
        self.assertIn("Which group numbers do you want to KEEP?", f["ASK_USER"])
        self.assertIn("go --yes --keep", f["NEXT"])
        self.assertEqual([e for e in journal.Journal(cmp.Phone(SERIAL).id).entries()], [])
        self.assertTrue(journal.backups(cmp.Phone(SERIAL).id))

    def test_second_call_removes_all_but_kept_groups_and_reports(self):
        out_path = os.path.join(self.tmp.name, "go.txt")
        self.run_cmp("go", "--out", out_path)
        groups = cmp.numbered_groups(out_path)
        keep_no = next(i for i, (g, p) in enumerate(groups, 1) if "com.coloros.weather2" in p)
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            code, out = self.run_cmp("go", "--yes", "--keep", str(keep_no), "--out", out_path)
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, out)
        self.assertIn("DONE", out)
        self.assertIn("undo everything: ./cmp restore", out)
        with open(self.env["CYP_FAKE_STATE"]) as f:
            state = json.load(f)
        gone = set(state["disabled"]) | set(state["uninstalled"]) | set(state["suspended"])
        self.assertIn("com.heytap.browser", gone)
        self.assertNotIn("com.coloros.weather2", gone, "kept group")
        self.assertEqual(state["settings"]["global/window_animation_scale"], "0.5")
        self.assertIn("~2× faster animations", out)

    def test_refused_settings_become_notes_not_questions(self):
        os.environ["CYP_FAKE_REFUSE_SETTINGS"] = "1"
        os.environ["CYP_FAKE_LOCKED"] = "1"
        try:
            out_path = os.path.join(self.tmp.name, "go.txt")
            self.run_cmp("go", "--out", out_path)
            cwd = os.getcwd()
            os.chdir(self.tmp.name)
            try:
                code, out = self.run_cmp("go", "--yes", "--keep", "none", "--adblock", "--out", out_path)
            finally:
                os.chdir(cwd)
        finally:
            os.environ.pop("CYP_FAKE_REFUSE_SETTINGS")
            os.environ.pop("CYP_FAKE_LOCKED")
        f = self.footer(out)
        self.assertEqual(code, 0, out)
        self.assertNotIn("ASK_USER", f)
        self.assertIn("note: couldn't change animations", out)
        self.assertIn("note: ad blocking skipped", out)


class TestRarelyUsed(FakePhoneTest):
    def test_go_lists_rarely_used_apps_with_letters(self):
        out_path = os.path.join(self.tmp.name, "go.txt")
        code, out = self.run_cmp("go", "--out", out_path)
        f = self.footer(out)
        self.assertIn("a. com.example.neveropened  (never opened)", out)
        self.assertIn("b. com.example.rarelyused  (91 days)", out)
        self.assertNotIn("com.whatsapp  (", out, "used today")
        self.assertNotIn("app.olauncher  (", out, "current home screen is protected")
        self.assertIn("UNINSTALL", f["ASK_USER"])
        self.assertIn("--uninstall", f["NEXT"])

    def test_go_uninstalls_picked_restricts_the_rest_and_restore_undoes_both(self):
        out_path = os.path.join(self.tmp.name, "go.txt")
        self.run_cmp("go", "--out", out_path)
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            code, out = self.run_cmp("go", "--yes", "--keep", "none", "--uninstall", "a", "--out", out_path)
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, out)
        with open(self.env["CYP_FAKE_STATE"]) as fh:
            state = json.load(fh)
        self.assertIn("com.example.neveropened", state["uninstalled"])
        self.assertEqual(state["buckets"]["com.example.rarelyused"], 45, "kept but rarely used: restricted")
        self.assertIn("blocked from the background: 1", out)
        code, out = self.run_cmp("restore")
        with open(self.env["CYP_FAKE_STATE"]) as fh:
            state = json.load(fh)
        self.assertNotIn("com.example.neveropened", state["uninstalled"])
        self.assertEqual(state["buckets"]["com.example.rarelyused"], 40)


class TestApprox(unittest.TestCase):
    def test_rounds_to_five_and_hides_noise(self):
        self.assertEqual(bench.approx(65.3), "~65%")
        self.assertEqual(bench.approx(-18.2), "~20%")
        self.assertEqual(bench.approx(4.0), "~5%")
        self.assertIsNone(bench.approx(2.0))

    def test_report_never_contains_raw_figures(self):
        before = {"ram_available_mb": 1993, "packages_installed": 335, "animation_scale": 1.0, "refresh_rate": 60.0}
        after = {"ram_available_mb": 3290, "packages_installed": 274, "animation_scale": 0.5, "refresh_rate": 90.0}
        lines = bench.compare(before, after)
        self.assertEqual(lines, ["~65% more free RAM", "~20% fewer apps", "~2× faster animations",
                                 "~50% smoother screen (90Hz)"])
        for raw in ("1993", "3290", "335", "274"):
            self.assertFalse(any(raw in l for l in lines))


class TestNoPhone(FakePhoneTest):
    fixture = "none"

    def test_doctor_asks_to_plug_in(self):
        code, out = self.run_cmp("doctor")
        f = self.footer(out)
        self.assertEqual((code, f["RESULT"]), (2, "stop"))
        self.assertIn("USB debugging", f["ASK_USER"])

    def test_probe_stops(self):
        code, out = self.run_cmp("probe")
        self.assertEqual(code, 2)
        self.assertIn("connect", self.footer(out)["NEXT"])


class TestUnauthorized(FakePhoneTest):
    fixture = "unauthorized"

    def test_doctor_asks_to_tap_allow(self):
        code, out = self.run_cmp("doctor")
        self.assertIn("Allow", self.footer(out)["ASK_USER"])


class TestMissingAdb(FakePhoneTest):
    def test_doctor_gives_install_command_not_a_question(self):
        os.environ["CYP_ADB"] = ""
        os.environ["PATH"], saved_path = "/nonexistent", os.environ["PATH"]
        orig = cmp.find_adb
        cmp.find_adb = lambda: None
        try:
            code, out = self.run_cmp("doctor")
        finally:
            cmp.find_adb = orig
            os.environ["PATH"] = saved_path
        f = self.footer(out)
        self.assertEqual(code, 2)
        self.assertIn("doctor", f["NEXT"])
        self.assertNotIn("ASK_USER", f)


class TestSafety(unittest.TestCase):
    def test_never_list(self):
        never = safety.load_never()
        self.assertTrue(safety.never_reason("android", never))
        self.assertTrue(safety.never_reason("com.google.android.overlay.modules.foo", never))
        self.assertTrue(safety.never_reason("com.android.ims.rcsservice", never))
        self.assertIsNone(safety.never_reason("com.glance.internet", never))

    def test_lint_catches_problems(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# header\ncom.glance.internet\ncom.glance.internet\ncom.android.phone  # no\nnot a package\n")
        try:
            problems = [p for _, _, p in safety.lint_list(f.name)]
        finally:
            os.unlink(f.name)
        self.assertEqual(problems, ["listed twice", "core package (data/never.txt)", "not a package name"])

    def test_all_device_lists_are_clean(self):
        lists = glob.glob(os.path.join(ROOT, "devices", "*", "debloat.txt"))
        self.assertTrue(lists)
        for path in lists:
            self.assertEqual(safety.lint_list(path), [], path)

    def test_device_folders_have_phone_json(self):
        for folder in glob.glob(os.path.join(ROOT, "devices", "*", "")):
            if not os.path.exists(os.path.join(folder, "debloat.txt")):
                continue
            with open(os.path.join(folder, "phone.json")) as f:
                meta = json.load(f)
            self.assertIn(meta["status"], ("tested", "starter"), folder)
            for key in ("phone", "brand", "os", "notes"):
                self.assertTrue(meta.get(key), "%s: %s" % (folder, key))


class TestJournal(unittest.TestCase):
    def test_append_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CYP_HOME"] = tmp
            try:
                j = journal.Journal("abc")
                j.append(pkg="com.glance.internet", method="disable", before="enabled", result="ok")
                j.append(pkg="com.heytap.market", method="suspend", before="enabled", result="ok")
                self.assertEqual([e["method"] for e in j.entries()], ["disable", "suspend"])
            finally:
                os.environ.pop("CYP_HOME")


if __name__ == "__main__":
    unittest.main()
