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
                for line in output.splitlines() if line.split(":", 1)[0] in ("RESULT", "SUMMARY", "NEXT", "ASK_USER", "ASK_JSON")}

    def adb_calls(self):
        with open(self.env["CYP_FAKE_LOG"], encoding="utf-8") as f:
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
        with open(os.path.join(snap, "meta.json"), encoding="utf-8") as f:
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
        with open(path, "w", encoding="utf-8") as f:
            f.write("# test list\n" + "\n".join(pkgs) + "\n")
        return path

    def phone_state(self):
        with open(self.env["CYP_FAKE_STATE"], encoding="utf-8") as f:
            return {k: (v if k in ("settings", "buckets") else set(v)) for k, v in json.load(f).items()}

    def save_phone_state(self, state):
        with open(self.env["CYP_FAKE_STATE"], "w", encoding="utf-8") as f:
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
        self.assertEqual(known["com.miui.msa.global"]["status"], "tested", "the Redmi 10 list confirms it")
        self.assertIn("ad service", known["com.miui.msa.global"]["note"])
        self.assertEqual(known["com.samsung.android.rubin.app"]["status"], "starter")

    def test_matching_device_by_model(self):
        self.assertEqual(kb.matching_device("RMX2151")[0], "realme-7-rmx2151")
        self.assertEqual(kb.matching_device("SM-A505F"), (None, None))

    def test_starter_lists_say_so(self):
        for folder, meta in kb.device_folders():
            if meta["status"] == "starter":
                with open(os.path.join(folder, "debloat.txt"), encoding="utf-8") as f:
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
        self.assertEqual(f["NEXT"], cmp.CMD + " tune --ui")
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
        questions = json.loads(f["ASK_JSON"])
        self.assertTrue(all(2 <= len(q["options"]) <= 4 and len(q["header"]) <= 12 for q in questions))
        self.assertEqual(questions[-1]["flag"], "--adblock")
        self.assertIn("--remove", {q["flag"] for q in questions})
        self.assertIn("ASK_JSON", f["ASK_USER"])
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
        self.assertIn("undo everything: %s restore" % cmp.CMD, out)
        with open(self.env["CYP_FAKE_STATE"], encoding="utf-8") as f:
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
        questions = json.loads(f["ASK_JSON"])
        apps = [o for q in questions if q["flag"] == "--uninstall" for o in q["options"]]
        self.assertEqual([o["value"] for o in apps], ["a", "b"])
        self.assertIn("91 days unused", apps[1]["description"])

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
        with open(self.env["CYP_FAKE_STATE"], encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertIn("com.example.neveropened", state["uninstalled"])
        self.assertEqual(state["buckets"]["com.example.rarelyused"], 45, "kept but rarely used: restricted")
        self.assertIn("blocked from the background: 1", out)
        code, out = self.run_cmp("restore")
        with open(self.env["CYP_FAKE_STATE"], encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertNotIn("com.example.neveropened", state["uninstalled"])
        self.assertEqual(state["buckets"]["com.example.rarelyused"], 40)


class TestReviewFixes(FakePhoneTest):
    """Regression tests for the second review round."""

    def go_once(self):
        out_path = os.path.join(self.tmp.name, "go.txt")
        self.run_cmp("go", "--out", out_path)
        return out_path

    def go_yes(self, out_path, *extra):
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            return self.run_cmp("go", "--yes", "--out", out_path, *extra)
        finally:
            os.chdir(cwd)

    def test_keep_by_mixed_case_package_name_keeps_it(self):
        out_path = self.go_once()
        code, out = self.go_yes(out_path, "--keep", "com.heytap.themestore")
        with open(self.env["CYP_FAKE_STATE"], encoding="utf-8") as f:
            state = json.load(f)
        self.assertNotIn("com.heytap.themestore", state["suspended"])

    def test_keep_with_a_typo_changes_nothing(self):
        out_path = self.go_once()
        code, out = self.go_yes(out_path, "--keep", "2-4")
        self.assertEqual(code, 2)
        self.assertIn("didn't understand --keep 2-4", self.footer(out)["SUMMARY"])
        self.assertEqual([e for e in journal.Journal(cmp.Phone(SERIAL).id).entries() if e["action"] == "remove"], [])

    def test_uninstall_with_a_bad_letter_stops(self):
        out_path = self.go_once()
        code, out = self.go_yes(out_path, "--uninstall", "z")
        self.assertEqual(code, 2)
        self.assertIn("--uninstall z", self.footer(out)["SUMMARY"])

    def test_first_call_deletes_stale_lists(self):
        out_path = os.path.join(self.tmp.name, "go.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("# Old group\ncom.whatsapp\n")
        self.run_cmp("go", "--out", out_path)
        self.assertNotIn("com.whatsapp", safety.read_list(out_path))

    def test_restore_last_after_go_undoes_every_uninstall(self):
        out_path = self.go_once()
        self.go_yes(out_path, "--keep", "1 2 3 4 5 6 7 8 9", "--uninstall", "a b")
        code, out = self.run_cmp("restore", "--last")
        with open(self.env["CYP_FAKE_STATE"], encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(set(state["uninstalled"]) & {"com.example.neveropened", "com.example.rarelyused"}, set())

    def test_rare_app_that_became_protected_is_left_alone(self):
        out_path = self.go_once()
        with open(out_path + ".rare.json", encoding="utf-8") as f:
            rare = json.load(f)
        rare.append("com.google.android.dialer")  # e.g. a list from another run, or a new default app
        with open(out_path + ".rare.json", "w", encoding="utf-8") as f:
            json.dump(rare, f)
        letter = cmp.LETTERS[len(rare) - 1]
        self.go_yes(out_path, "--keep", "none", "--uninstall", letter)
        with open(self.env["CYP_FAKE_STATE"], encoding="utf-8") as f:
            state = json.load(f)
        self.assertNotIn("com.google.android.dialer", state["uninstalled"])

    def test_apply_skips_owner_installed_copies(self):
        self.run_cmp("backup")
        path = os.path.join(self.tmp.name, "l.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("com.linkedin.android\n")
        code, out = self.run_cmp("apply", path, "--yes")
        self.assertIn("installed by you, not preinstalled; kept", out)


class TestJournalEdgeCases(unittest.TestCase):
    def test_pending_removal_is_restorable(self):
        import actions
        entries = [{"action": "remove", "pkg": "a.b", "method": "disable", "result": "pending"}]
        self.assertEqual(actions.active_removals(entries), [("a.b", "disable")])
        entries.append({"action": "remove", "pkg": "a.b", "method": "disable", "result": "refused"})
        self.assertEqual(actions.active_removals(entries), [])

    def test_refused_setting_is_not_active_but_pending_is(self):
        import actions
        base = {"action": "setting", "ns": "global", "key": "k", "before": "1.0"}
        self.assertEqual(actions.active_settings([dict(base, result="pending")]), [("global", "k", "1.0")])
        self.assertEqual(actions.active_settings([dict(base, result="pending"), dict(base, result="refused")]), [])
        self.assertEqual(actions.active_settings([dict(base, result="pending"), dict(base, result="refused"),
                                                  dict(base, result="ok")]), [("global", "k", "1.0")])

    def test_truncated_journal_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CYP_HOME"] = tmp
            try:
                j = journal.Journal("x")
                j.append(pkg="a.b", action="remove", method="disable", result="ok")
                with open(j.path, "a", encoding="utf-8") as f:
                    f.write('{"pkg": "c.d", "act')
                self.assertEqual(len(j.entries()), 1)
            finally:
                os.environ.pop("CYP_HOME")

    def test_keep_lines_are_not_headings(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# Ads\n# keep: a.one\nb.two\n")
        try:
            self.assertEqual(kb.parse_list(f.name), [("b.two", "Ads", "")])
        finally:
            os.unlink(f.name)


class TestCheckboxQuestions(FakePhoneTest):
    def test_split_even_never_leaves_a_lonely_option(self):
        self.assertEqual([len(p) for p in cmp.split_even(list(range(5)))], [3, 2])
        self.assertEqual([len(p) for p in cmp.split_even(list(range(20)))], [4, 4, 4, 4, 4])
        self.assertEqual([len(p) for p in cmp.split_even(list(range(9)))], [3, 3, 3])
        for n in range(2, 40):
            self.assertTrue(all(2 <= len(p) <= 4 for p in cmp.split_even(list(range(n)))), n)

    def test_single_item_becomes_a_remove_or_keep_choice(self):
        [q] = cmp.checkbox_questions("Remove?", "Apps", "--uninstall", [("a", "Spotify", "x")], "")
        self.assertFalse(q["multi_select"])
        self.assertEqual([o["value"] for o in q["options"]], ["a", ""])

    def test_ticked_groups_are_removed_unticked_kept(self):
        out_path = os.path.join(self.tmp.name, "go.txt")
        code, out = self.run_cmp("go", "--out", out_path)
        questions = json.loads(self.footer(out)["ASK_JSON"])
        groups = cmp.numbered_groups(out_path)
        weather_group = str(next(i for i, (g, p) in enumerate(groups, 1) if "com.coloros.weather2" in p))
        others = [o["value"] for q in questions if q["flag"] == "--remove" for o in q["options"]
                  if o["value"] != weather_group]
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            code, out = self.run_cmp("go", "--yes", "--remove", " ".join(others), "--adblock", "yes",
                                     "--out", out_path)
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, out)
        with open(self.env["CYP_FAKE_STATE"], encoding="utf-8") as f:
            state = json.load(f)
        gone = set(state["disabled"]) | set(state["uninstalled"]) | set(state["suspended"])
        self.assertIn("com.heytap.browser", gone)
        self.assertNotIn("com.coloros.weather2", gone)
        self.assertEqual(state["settings"].get("global/private_dns_mode"), "hostname")

    def test_bad_remove_token_changes_nothing(self):
        out_path = os.path.join(self.tmp.name, "go.txt")
        self.run_cmp("go", "--out", out_path)
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            code, out = self.run_cmp("go", "--yes", "--remove", "99", "--out", out_path)
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 2)
        self.assertIn("--remove 99", self.footer(out)["SUMMARY"])


class TestResume(FakePhoneTest):
    """A stop part-way through `go --yes` must be continued by `go`, without new questions or measurements."""

    def run_in_tmp(self, *argv):
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            return self.run_cmp(*argv)
        finally:
            os.chdir(cwd)

    def test_reboot_failure_then_go_resumes_without_asking(self):
        out_path = os.path.join(self.tmp.name, "go.txt")
        self.run_cmp("go", "--out", out_path)
        import phone as phone_mod
        real = phone_mod.Phone.reboot_and_wait
        phone_mod.Phone.reboot_and_wait = lambda self, timeout=0: False
        try:
            code, out = self.run_in_tmp("go", "--yes", "--remove", "1", "--uninstall", "a", "--out", out_path)
        finally:
            phone_mod.Phone.reboot_and_wait = real
        f = self.footer(out)
        self.assertEqual(code, 2)
        self.assertEqual(f["NEXT"], cmp.CMD + " go")
        self.assertIn("picks up where it stopped", out)
        bench_dir = os.path.join(self.tmp.name, cmp.Phone(SERIAL).id, "bench")
        befores = [b for b in os.listdir(bench_dir) if b.endswith("-before.json")]
        removes = len([e for e in journal.Journal(cmp.Phone(SERIAL).id).entries() if e["action"] == "remove"])

        code, out = self.run_in_tmp("go", "--out", out_path)
        f = self.footer(out)
        self.assertEqual(code, 0, out)
        self.assertIn("Picking it up", out)
        self.assertNotIn("ASK_JSON", f)
        self.assertIn("DONE", out)
        self.assertEqual(len([b for b in os.listdir(bench_dir) if b.endswith("-before.json")]), len(befores),
                         "no second 'before' measurement")
        self.assertEqual(len([e for e in journal.Journal(cmp.Phone(SERIAL).id).entries()
                              if e["action"] == "remove"]), removes, "nothing removed twice")
        self.assertIn("removed: ", out)
        self.assertNotIn("removed: 0 app", out, "totals from the first part are kept")

        code, out = self.run_cmp("go", "--out", out_path)
        self.assertIn("ASK_JSON", self.footer(out), "after DONE, the next go starts fresh")

    def test_go_yes_without_go_first_points_to_go(self):
        code, out = self.run_cmp("go", "--yes")
        self.assertEqual(self.footer(out)["NEXT"], cmp.CMD + " go")

    def test_uninstall_falls_back_to_disable_when_blocked(self):
        out_path = os.path.join(self.tmp.name, "go.txt")
        self.run_cmp("go", "--out", out_path)
        fx = os.path.join(ROOT, "tests", "fixtures", "realme7.json")
        with open(fx, encoding="utf-8") as fh:
            data = json.load(fh)
        with open(out_path + ".rare.json", encoding="utf-8") as fh:
            rare = json.load(fh)
        letter = cmp.LETTERS[rare.index("com.example.rarelyused")]
        data["refuse"]["uninstall"].append("com.example.rarelyused")
        alt = os.path.join(ROOT, "tests", "fixtures", "realme7-miui.json")
        with open(alt, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.environ["CYP_FAKE_PHONE"] = "realme7-miui"
        try:
            code, out = self.run_in_tmp("go", "--yes", "--keep", "none", "--uninstall", letter, "--out", out_path)
        finally:
            os.environ["CYP_FAKE_PHONE"] = "realme7"
            os.remove(alt)
        self.assertIn("disabled (the phone blocks uninstalling it)", out)


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

    def test_doctor_stops_with_steps_and_no_question(self):
        cmp.wait_for_access.__defaults__ = (0, 0)
        try:
            code, out = self.run_cmp("doctor")
        finally:
            cmp.wait_for_access.__defaults__ = (10, 45)
        f = self.footer(out)
        self.assertEqual((code, f["RESULT"]), (2, "stop"))
        self.assertIn("ERROR: no phone with USB debugging found", out)
        self.assertIn("Build number", out)
        self.assertIn("run again", f["SUMMARY"])
        self.assertNotIn("ASK_USER", f)
        self.assertEqual(f["NEXT"], cmp.CMD + " go", "the way back is always go, which resumes")

    def test_go_stops_the_same_way(self):
        cmp.wait_for_access.__defaults__ = (0, 0)
        try:
            code, out = self.run_cmp("go")
        finally:
            cmp.wait_for_access.__defaults__ = (10, 45)
        self.assertEqual(code, 2)
        self.assertNotIn("ASK_USER", self.footer(out))

    def test_probe_stops(self):
        code, out = self.run_cmp("probe")
        self.assertEqual(code, 2)
        self.assertIn("no phone with USB debugging found", self.footer(out)["SUMMARY"])


class TestUnauthorized(FakePhoneTest):
    fixture = "unauthorized"

    def test_doctor_waits_for_allow_then_stops(self):
        cmp.wait_for_access.__defaults__ = (0, 0)
        try:
            code, out = self.run_cmp("doctor")
        finally:
            cmp.wait_for_access.__defaults__ = (10, 45)
        self.assertEqual(code, 2)
        self.assertIn("tap 'Allow'", out)
        self.assertIn("hasn't allowed this computer", self.footer(out)["SUMMARY"])
        self.assertNotIn("ASK_USER", self.footer(out))


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
            with open(os.path.join(folder, "phone.json"), encoding="utf-8") as f:
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
