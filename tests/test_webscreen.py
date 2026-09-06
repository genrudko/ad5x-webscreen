import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import webscreen


class TestConfig(unittest.TestCase):
    def test_defaults_are_safe_and_match_tested_profile(self):
        cfg = webscreen.WebScreenConfig.defaults()
        self.assertEqual(cfg.bind, "0.0.0.0")
        self.assertEqual(cfg.port, 8010)
        self.assertEqual(cfg.poll_hz, 10.0)
        self.assertEqual(cfg.jpeg_quality, 75)
        self.assertEqual(cfg.nice, 10)
        self.assertTrue(cfg.touch_allowed)
        self.assertFalse(cfg.touch_start_enabled)
        self.assertEqual(cfg.touch_failsafe_s, 1.0)
        self.assertEqual(cfg.touch_pressure, 1200)
        self.assertFalse(hasattr(cfg, "control_token_path"))

    def test_ini_overrides_and_validates(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "webscreen.ini"
            p.write_text(
                """
[server]
bind = 127.0.0.1
port = 18010
poll_hz = 5
jpeg_quality = 82
nice = 7

[touch]
allowed = false
start_enabled = false
failsafe_seconds = 1.5
pressure = 900
helix_settings = /tmp/helix.json

""".strip(),
                encoding="utf-8",
            )
            cfg = webscreen.WebScreenConfig.load(p)
            self.assertEqual(cfg.bind, "127.0.0.1")
            self.assertEqual(cfg.port, 18010)
            self.assertEqual(cfg.poll_hz, 5.0)
            self.assertEqual(cfg.jpeg_quality, 82)
            self.assertEqual(cfg.nice, 7)
            self.assertFalse(cfg.touch_allowed)
            self.assertEqual(cfg.touch_failsafe_s, 1.5)
            self.assertEqual(cfg.touch_pressure, 900)
            self.assertEqual(cfg.helix_settings, "/tmp/helix.json")

    def test_invalid_port_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "webscreen.ini"
            p.write_text("[server]\nport=70000\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                webscreen.WebScreenConfig.load(p)


class TestCalibration(unittest.TestCase):
    HELIX = {
        "input": {
            "calibration": {
                "valid": True,
                "a": 1.16876,
                "b": -0.00922702,
                "c": -72.6939,
                "d": 0.0549667,
                "e": 1.46272,
                "f": -122.294,
            }
        }
    }

    def test_loads_input_calibration_and_round_trips(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "settings.json"
            p.write_text(json.dumps(self.HELIX), encoding="utf-8")
            cal, source = webscreen.AffineCalibration.from_helix_file(p)
            self.assertEqual(source, "input.calibration")
            raw_x, raw_y = cal.inverse(400, 240)
            screen_x, screen_y = cal.forward(raw_x, raw_y)
            self.assertAlmostEqual(screen_x, 400, places=6)
            self.assertAlmostEqual(screen_y, 240, places=6)

    def test_display_calibration_is_fallback(self):
        payload = {"display": {"calibration": {
            "valid": True, "a": 1, "b": 0, "c": 0,
            "d": 0, "e": 1, "f": 0,
        }}}
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "settings.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            cal, source = webscreen.AffineCalibration.from_helix_file(p)
            self.assertEqual(source, "display.calibration")
            self.assertEqual(cal.inverse(123, 45), (123.0, 45.0))

    def test_invalid_or_singular_calibration_is_rejected(self):
        payload = {"input": {"calibration": {
            "valid": True, "a": 1, "b": 2, "c": 0,
            "d": 2, "e": 4, "f": 0,
        }}}
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "settings.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                webscreen.AffineCalibration.from_helix_file(p)


class TestTouchMapping(unittest.TestCase):
    def test_mapper_uses_inverse_calibration_and_real_axis_ranges(self):
        cal = webscreen.AffineCalibration(2, 0, 10, 0, 2, 20)
        mapper = webscreen.TouchMapper(
            cal,
            webscreen.AxisRange(0, 800),
            webscreen.AxisRange(0, 480),
        )
        self.assertEqual(mapper.map_screen(410, 260), (200, 120))
        self.assertEqual(mapper.map_screen(-1000, -1000), (0, 0))
        self.assertEqual(mapper.map_screen(10000, 10000), (800, 480))

    def test_mips_eviocgabs_request_matches_printer_probe(self):
        self.assertEqual(webscreen.eviocgabs_request(0, "mips"), 0x40184540)
        self.assertEqual(webscreen.eviocgabs_request(1, "mips"), 0x40184541)
        self.assertEqual(webscreen.eviocgabs_request(24, "mips"), 0x40184558)
        self.assertEqual(webscreen.eviocgabs_request(0, "x86_64"), 0x80184540)


class TestSecurityAndUi(unittest.TestCase):
    def test_ui_has_no_control_token_friction(self):
        html = webscreen.render_index_html().decode("utf-8")
        self.assertIn("img.naturalWidth", html)
        self.assertIn("img.naturalHeight", html)
        self.assertIn("Enable remote touch", html)
        self.assertNotIn("X-WebScreen-Token", html)
        self.assertNotIn("sessionStorage", html)
        self.assertNotIn("control token", html.lower())
        self.assertNotIn("prompt(", html)

    def test_fluidd_iframe_surface_is_clean_and_directly_interactive(self):
        html = webscreen.render_iframe_html().decode("utf-8")
        self.assertIn('src="streams"', html)
        self.assertIn("touch?e=", html)
        self.assertNotIn("touch/enable", html)
        self.assertNotIn("X-WebScreen-Token", html)
        self.assertNotIn("sessionStorage", html)
        self.assertNotIn("<button", html)
        self.assertNotIn("<pre", html)


class TestFramebufferGeometry(unittest.TestCase):
    def test_contiguous_visible_page_plan_uses_yoffset_and_stride(self):
        geom = webscreen.FrameGeometry(
            xres=800,
            yres=480,
            xres_virtual=800,
            yres_virtual=960,
            xoffset=0,
            yoffset=480,
            bits_per_pixel=32,
            stride=3200,
        )
        plan = geom.read_plan()
        self.assertEqual(plan, [(480 * 3200, 480 * 3200)])

    def test_noncontiguous_plan_reads_visible_width_row_by_row(self):
        geom = webscreen.FrameGeometry(
            xres=100,
            yres=2,
            xres_virtual=128,
            yres_virtual=2,
            xoffset=3,
            yoffset=0,
            bits_per_pixel=32,
            stride=512,
        )
        self.assertEqual(geom.read_plan(), [(12, 400), (524, 400)])


if __name__ == "__main__":
    unittest.main()

class TestRuntimeHelpers(unittest.TestCase):
    def test_touch_device_discovery_prefers_tsc2007(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            sys_root = root / "sys/class/input"
            dev_root = root / "dev/input"
            dev_root.mkdir(parents=True)
            for ev, name in (("event2", "Generic Touchscreen"), ("event1", "TSC2007 Touchscreen")):
                (dev_root / ev).touch()
                d = sys_root / ev / "device"
                d.mkdir(parents=True)
                (d / "name").write_text(name + "\n", encoding="utf-8")
            dev, name = webscreen.find_touch_device(sys_root=sys_root, dev_root=dev_root)
            self.assertEqual(pathlib.Path(dev).name, "event1")
            self.assertEqual(name, "TSC2007 Touchscreen")

    def test_touch_sequences_include_pressure_and_release(self):
        down = webscreen.touch_down_events(100, 200, 1200)
        self.assertEqual(down[0], (webscreen.EV_KEY, webscreen.BTN_TOUCH, 1))
        self.assertIn((webscreen.EV_ABS, webscreen.ABS_X, 100), down)
        self.assertIn((webscreen.EV_ABS, webscreen.ABS_Y, 200), down)
        self.assertIn((webscreen.EV_ABS, webscreen.ABS_PRESSURE, 1200), down)
        self.assertEqual(down[-1], (webscreen.EV_SYN, webscreen.SYN_REPORT, 0))
        up = webscreen.touch_up_events()
        self.assertIn((webscreen.EV_ABS, webscreen.ABS_PRESSURE, 0), up)
        self.assertIn((webscreen.EV_KEY, webscreen.BTN_TOUCH, 0), up)
        self.assertEqual(up[-1], (webscreen.EV_SYN, webscreen.SYN_REPORT, 0))

    def test_touch_start_enabled_true_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "webscreen.ini"
            p.write_text("[touch]\nstart_enabled=true\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                webscreen.WebScreenConfig.load(p)

class TestInputAbi(unittest.TestCase):
    def test_decode_abs_info_exposes_axis_range(self):
        import struct
        raw = struct.pack("=6i", 12, 0, 800, 1, 2, 3)
        info = webscreen.decode_abs_info(raw)
        self.assertEqual(info.value, 12)
        self.assertEqual(info.minimum, 0)
        self.assertEqual(info.maximum, 800)
        self.assertEqual(info.axis_range, webscreen.AxisRange(0, 800))
        self.assertEqual(info.fuzz, 1)
        self.assertEqual(info.flat, 2)
        self.assertEqual(info.resolution, 3)

    def test_event_pack_round_trips_native_layout(self):
        import struct
        payload = webscreen.pack_input_event(1.25, webscreen.EV_ABS, webscreen.ABS_X, 321)
        self.assertEqual(len(payload), struct.calcsize("@llHHi"))
        sec, usec, ev_type, code, value = struct.unpack("@llHHi", payload)
        self.assertEqual(sec, 1)
        self.assertEqual(usec, 250000)
        self.assertEqual((ev_type, code, value), (webscreen.EV_ABS, webscreen.ABS_X, 321))

class TestServiceSurface(unittest.TestCase):
    def test_runtime_components_are_constructible_without_touching_hardware(self):
        cfg = webscreen.WebScreenConfig.defaults()
        state = webscreen.RuntimeState(cfg)
        self.assertEqual(state.config.port, 8010)
        self.assertFalse(state.touch_enabled)
        self.assertEqual(state.clients, 0)
        self.assertTrue(callable(webscreen.main))
        self.assertTrue(hasattr(webscreen, "FramebufferDevice"))
        self.assertTrue(hasattr(webscreen, "TouchController"))


class TestHttpSurface(unittest.TestCase):
    class FakeState:
        def __init__(self):
            import threading
            self.lock = threading.RLock()
            self.touch_enabled = False
            self.touch_error = ""

    class FakeTouch:
        def __init__(self, state):
            self.state = state
            self.enabled_calls = 0
            self.down_calls = []

        def enable(self):
            self.enabled_calls += 1
            self.state.touch_enabled = True

        def disable(self):
            self.state.touch_enabled = False

        def down(self, x, y):
            self.down_calls.append((x, y))

        def move(self, x, y):
            pass

        def up(self):
            pass

    class FakeApp:
        def __init__(self):
            import types
            self.config = types.SimpleNamespace(touch_allowed=True)
            self.state = TestHttpSurface.FakeState()
            self.touch = TestHttpSurface.FakeTouch(self.state)

        def clamp_screen_coords(self, x, y):
            return x, y

    def _serve(self):
        import threading
        app = self.FakeApp()
        server = webscreen.WebScreenHTTPServer(
            ("127.0.0.1", 0), webscreen.WebScreenRequestHandler, app
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return app, server, thread

    def test_stream_endpoint_is_iframe_html(self):
        import urllib.request
        app, server, thread = self._serve()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/stream", timeout=2
            ) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn('src="streams"', body)
                self.assertNotIn("control token", body.lower())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_first_touch_down_auto_arms_without_auth(self):
        import urllib.request
        app, server, thread = self._serve()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/touch?e=down&x=123&y=45",
                data=b"",
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(app.touch.enabled_calls, 1)
            self.assertEqual(app.touch.down_calls, [(123.0, 45.0)])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class TestProbeDegradation(unittest.TestCase):
    class FakeFramebuffer:
        def open(self):
            pass

        def geometry(self):
            return webscreen.FrameGeometry(
                xres=800, yres=480, xres_virtual=800, yres_virtual=960,
                xoffset=0, yoffset=0, bits_per_pixel=32, stride=3200,
            )

        def close(self):
            pass

    class BrokenTouch:
        def probe(self, require_calibration=False):
            raise RuntimeError("touch unavailable")

        def shutdown(self):
            pass

    def _service(self):
        service = webscreen.WebScreenService(webscreen.WebScreenConfig.defaults())
        service.framebuffer = self.FakeFramebuffer()
        service.touch = self.BrokenTouch()
        return service

    def test_video_probe_degrades_when_touch_probe_fails(self):
        service = self._service()
        service.probe(require_touch=False)
        self.assertEqual(service.state.geometry.xres, 800)
        self.assertIn("touch unavailable", service.state.touch_error)

    def test_check_probe_still_requires_working_touch(self):
        service = self._service()
        with self.assertRaisesRegex(RuntimeError, "touch unavailable"):
            service.probe(require_touch=True)
