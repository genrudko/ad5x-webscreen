import pathlib
import os
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestPackaging(unittest.TestCase):
    def test_required_plugin_files_exist(self):
        for name in (
            "ad5x_webscreen.cfg",
            "install.sh",
            "uninstall.sh",
            "update.sh",
            "power_on_hook.sh",
            "S71ad5x_webscreen",
            "webscreen.ini.example",
            "moonraker.update.conf",
            "control.sh",
            "README.md",
            "VERSION",
        ):
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file(), name)

    def test_default_config_matches_validated_runtime_profile(self):
        text = (ROOT / "webscreen.ini.example").read_text(encoding="utf-8")
        self.assertIn("port = 8010", text)
        self.assertIn("poll_hz = 10", text)
        self.assertIn("jpeg_quality = 75", text)
        self.assertIn("nice = 10", text)
        self.assertIn("allowed = true", text)
        self.assertIn("start_enabled = false", text)
        self.assertIn("failsafe_seconds = 1.0", text)
        self.assertIn("pressure = 1200", text)

    def test_init_script_has_safe_lifecycle_and_port_preflight(self):
        text = (ROOT / "S71ad5x_webscreen").read_text(encoding="utf-8")
        self.assertIn("/var/run/ad5x_webscreen.pid", text)
        self.assertIn("port_in_use", text)
        self.assertIn("already in use", text)
        self.assertIn("start)", text)
        self.assertIn("stop)", text)
        self.assertIn("restart", text)
        self.assertIn("status)", text)
        self.assertNotIn("webscreen_poc", text)

    def test_install_registers_update_manager_without_modifying_zmod_sources(self):
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("plugins.moonraker.conf", install)
        self.assertIn("moonraker.update.conf", install)
        self.assertIn("S71ad5x_webscreen", install)
        self.assertNotIn("zmod/.shell/root/start.sh", install)
        self.assertNotIn("sed -i", install.replace("sed -i '/ad5x_webscreen/d'", ""))



    def test_power_on_hook_uses_zmod_user_power_on_file_with_managed_markers(self):
        text = (ROOT / "power_on_hook.sh").read_text()
        self.assertIn("/usr/data/config/mod_data/power_on.sh", text)
        self.assertIn("AD5X WebScreen", text)
        self.assertIn("control.sh", text)
        self.assertIn("install)", text)
        self.assertIn("remove)", text)
        self.assertNotIn("/usr/data/zmod/zmod/.shell/root/start.sh", text)
        self.assertNotIn("/usr/prog/app_startup.sh", text)


    def test_power_on_hook_install_remove_is_idempotent_and_preserves_other_content(self):
        hook = ROOT / "power_on_hook.sh"
        with tempfile.TemporaryDirectory() as td:
            power_on = pathlib.Path(td) / "power_on.sh"
            power_on.write_text("#!/bin/sh\necho before\n", encoding="utf-8")
            env = os.environ.copy()
            env["AD5X_WEBSCREEN_POWER_ON"] = str(power_on)

            for _ in range(2):
                subprocess.run(["sh", str(hook), "install"], check=True, env=env, capture_output=True, text=True)
            text = power_on.read_text(encoding="utf-8")
            self.assertEqual(text.count("# >>> AD5X WebScreen >>>"), 1)
            self.assertEqual(text.count("# <<< AD5X WebScreen <<<"), 1)
            self.assertIn("echo before", text)

            subprocess.run(["sh", str(hook), "check"], check=True, env=env)
            subprocess.run(["sh", str(hook), "remove"], check=True, env=env, capture_output=True, text=True)
            text = power_on.read_text(encoding="utf-8")
            self.assertNotIn("AD5X WebScreen", text)
            self.assertIn("echo before", text)

    def test_install_and_uninstall_manage_power_on_hook(self):
        install = (ROOT / "install.sh").read_text()
        uninstall = (ROOT / "uninstall.sh").read_text()
        self.assertIn('power_on_hook.sh" install', install)
        self.assertIn('power_on_hook.sh" remove', uninstall)

    def test_update_hook_restarts_only_webscreen_service(self):
        text = (ROOT / "update.sh").read_text()
        self.assertIn("S71ad5x_webscreen", text)
        self.assertIn("restart", text)
        self.assertNotIn("klipper", text.lower())
        self.assertNotIn("moonraker restart", text.lower())

    def test_update_manager_targets_standalone_repository(self):
        text = (ROOT / "moonraker.update.conf").read_text(encoding="utf-8")
        self.assertIn("[update_manager ad5x_webscreen]", text)
        self.assertIn("https://github.com/genrudko/ad5x-webscreen.git", text)
        self.assertIn("primary_branch: main", text)
        self.assertIn("/root/printer_data/config/mod_data/plugins/ad5x_webscreen", text)

    def test_uninstall_stops_service_and_removes_only_plugin_owned_hooks(self):
        text = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
        self.assertIn("S71ad5x_webscreen", text)
        self.assertIn("plugins.moonraker.conf", text)
        self.assertIn("ad5x_webscreen", text)
        self.assertNotIn("rm -rf /usr/data/zmod", text)


if __name__ == "__main__":
    unittest.main()
