import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "configure_webcam.sh"
TEMPLATE = ROOT / "moonraker.webcam.conf.template"


class TestWebcamConfig(unittest.TestCase):
    def _run(self, output, ip=None, restart_marker=None, path=None):
        env = os.environ.copy()
        env["AD5X_WEBSCREEN_WEBCAM_OUTPUT"] = str(output)
        env["AD5X_WEBSCREEN_WEBCAM_TEMPLATE"] = str(TEMPLATE)
        if ip is not None:
            env["AD5X_WEBSCREEN_PRINTER_IP"] = ip
        else:
            env.pop("AD5X_WEBSCREEN_PRINTER_IP", None)
        if restart_marker is not None:
            restart = output.parent / "restart-moonraker.sh"
            restart.write_text(
                "#!/bin/sh\nprintf 'restart\\n' >> \"$AD5X_WEBSCREEN_RESTART_MARKER\"\n",
                encoding="utf-8",
            )
            restart.chmod(0o755)
            env["AD5X_WEBSCREEN_MOONRAKER_RESTART_CMD"] = str(restart)
            env["AD5X_WEBSCREEN_RESTART_MARKER"] = str(restart_marker)
        if path is not None:
            env["PATH"] = path
        args = ["sh", str(SCRIPT)]
        if restart_marker is not None:
            args.append("--restart-if-changed")
        return subprocess.run(
            args,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_generates_absolute_fluidd_urls_and_restarts_on_change(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            output = td / "moonraker.webcam.runtime.conf"
            marker = td / "restart.log"
            result = self._run(output, ip="192.168.1.106", restart_marker=marker)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = output.read_text(encoding="utf-8")
            self.assertIn("[webcam screen]", text)
            self.assertIn("service: iframe", text)
            self.assertIn("stream_url: http://192.168.1.106:8010/stream", text)
            self.assertIn("snapshot_url: http://192.168.1.106:8010/snapshot", text)
            self.assertIn("aspect_ratio: 800:480", text)
            self.assertEqual(marker.read_text(encoding="utf-8"), "restart\n")

    def test_same_rendered_config_does_not_restart_moonraker_again(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            output = td / "moonraker.webcam.runtime.conf"
            marker = td / "restart.log"
            first = self._run(output, ip="192.168.1.106", restart_marker=marker)
            second = self._run(output, ip="192.168.1.106", restart_marker=marker)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "restart\n")
            self.assertIn("unchanged", second.stdout.lower())

    def test_ip_change_regenerates_config_and_restarts_moonraker(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            output = td / "moonraker.webcam.runtime.conf"
            marker = td / "restart.log"
            first = self._run(output, ip="192.168.1.106", restart_marker=marker)
            second = self._run(output, ip="192.168.1.107", restart_marker=marker)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("192.168.1.107:8010/stream", output.read_text(encoding="utf-8"))
            self.assertEqual(marker.read_text(encoding="utf-8"), "restart\nrestart\n")

    def test_detects_ipv4_from_default_route_interface(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            output = td / "moonraker.webcam.runtime.conf"
            fakebin = td / "bin"
            fakebin.mkdir()
            fake_ip = fakebin / "ip"
            fake_ip.write_text(
                "#!/bin/sh\n"
                "case \"$*\" in\n"
                "  route) echo 'default via 192.168.1.1 dev wlan0' ;;\n"
                "  '-4 addr show dev wlan0') echo '3: wlan0: <UP>'; echo '    inet 192.168.1.123/24 brd 192.168.1.255 scope global wlan0' ;;\n"
                "  '-4 addr show') echo '3: wlan0: <UP>'; echo '    inet 192.168.1.123/24 brd 192.168.1.255 scope global wlan0' ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_ip.chmod(0o755)
            result = self._run(output, path=f"{fakebin}:/usr/bin:/bin")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("192.168.1.123:8010/stream", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
