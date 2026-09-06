# AD5X WebScreen v0.1.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a standalone Z-Mod plugin that mirrors the Flashforge AD5X framebuffer over MJPEG and provides opt-in remote touch with conservative CPU and lifecycle behavior.

**Architecture:** `webscreen.py` owns framebuffer capture, change detection, MJPEG HTTP serving, touch discovery/calibration, and guarded evdev injection. Shell scripts provide Z-Mod install/update/uninstall, SysV service control, and user `power_on.sh` autostart without modifying Z-Mod source files. Runtime configuration and the control token live outside the Git checkout.

**Tech Stack:** Python 3, Pillow, Linux framebuffer/evdev ioctls, BusyBox/POSIX shell, Z-Mod plugin lifecycle, Moonraker update_manager.

**Spec:** `README.md`

## Global Constraints

- Target printer is Flashforge AD5X only.
- Default HTTP port is `8010`.
- Default profile is `poll_hz = 10`, `jpeg_quality = 75`, `nice = 10`.
- Framebuffer capture must stop when there are no stream clients.
- JPEG encoding must happen only when the framebuffer content changes.
- Remote touch must start disabled on every service start.
- Touch control endpoints require the generated control token.
- Touch injection requires valid HelixScreen affine calibration; no unsafe direct-coordinate fallback.
- Touch release must be forced on failsafe timeout and process termination.
- Z-Mod source files, `/usr/prog/app_startup.sh`, and Z-Mod root startup scripts must not be modified.
- Autostart must be registered through the Z-Mod user `mod_data/power_on.sh` hook.
- Install/update/uninstall must affect only WebScreen-owned files and managed blocks.

---

### Task 1: Runtime capture and HTTP surface

**Files:**
- Create: `webscreen.py`
- Create: `webscreen.ini.example`
- Test: `tests/test_webscreen.py`

**Interfaces:**
- Consumes: `/dev/fb0`, Linux framebuffer ioctls, Pillow.
- Produces: `WebScreenConfig`, `FramebufferDevice`, `FrameGeometry`, `WebScreenService`, `/stream`, `/snapshot`, and `main()`.

- [ ] **Step 1: Write failing tests for safe defaults and framebuffer geometry.**

```python
cfg = webscreen.WebScreenConfig.defaults()
assert cfg.port == 8010
assert cfg.poll_hz == 10.0
assert cfg.jpeg_quality == 75
assert cfg.nice == 10
assert cfg.touch_start_enabled is False
```

- [ ] **Step 2: Run the tests and verify RED.**

Run: `python3 -m unittest tests.test_webscreen.TestConfig tests.test_webscreen.TestFramebufferGeometry -v`

Expected before implementation: import/API failures for missing runtime types.

- [ ] **Step 3: Implement config parsing, framebuffer geometry, visible-page reads, client-aware polling, change detection, and MJPEG/snapshot serving.**

```python
# Required runtime invariant:
if state.clients == 0:
    wait_for_client()
else:
    frame = framebuffer.read_visible()
    if frame != previous_frame:
        jpeg = encode_jpeg(frame, quality=config.jpeg_quality)
```

- [ ] **Step 4: Run runtime tests and verify GREEN.**

Run: `python3 -m unittest tests.test_webscreen.TestConfig tests.test_webscreen.TestFramebufferGeometry tests.test_webscreen.TestServiceSurface -v`

Expected: all selected tests pass.

### Task 2: Safe remote touch

**Files:**
- Modify: `webscreen.py`
- Test: `tests/test_webscreen.py`

**Interfaces:**
- Consumes: TSC2007/Touchscreen sysfs discovery, EVIOCGABS ranges, `/srv/helixscreen/config/settings.json` affine calibration.
- Produces: `AffineCalibration`, `TouchMapper`, `TouchController`, guarded touch-control HTTP endpoints.

- [ ] **Step 1: Write failing tests for dynamic device selection, MIPS ioctl encoding, affine inverse mapping, exact token checks, and forced release.**

```python
mapper = webscreen.TouchMapper(calibration, webscreen.AxisRange(0, 800), webscreen.AxisRange(0, 480))
assert mapper.map_screen(410, 260) == (200, 120)
```

- [ ] **Step 2: Run the touch tests and verify RED.**

Run: `python3 -m unittest tests.test_webscreen.TestCalibration tests.test_webscreen.TestTouchMapping tests.test_webscreen.TestRuntimeHelpers tests.test_webscreen.TestSecurityAndUi -v`

Expected before implementation: missing calibration/touch APIs.

- [ ] **Step 3: Implement dynamic touch discovery, EVIOCGABS decoding, inverse affine mapping, DOWN/MOVE/UP injection, pressure, token checks, and failsafe release.**

```python
if not config.touch_allowed or not state.touch_enabled:
    reject_touch()
if not hmac.compare_digest(request_token, control_token):
    reject_control()
```

- [ ] **Step 4: Run touch tests and verify GREEN.**

Run: `python3 -m unittest tests.test_webscreen.TestCalibration tests.test_webscreen.TestTouchMapping tests.test_webscreen.TestRuntimeHelpers tests.test_webscreen.TestSecurityAndUi tests.test_webscreen.TestProbeDegradation -v`

Expected: all selected tests pass.

### Task 3: Z-Mod lifecycle and autostart

**Files:**
- Create: `S71ad5x_webscreen`
- Create: `control.sh`
- Create: `install.sh`
- Create: `update.sh`
- Create: `uninstall.sh`
- Create: `power_on_hook.sh`
- Create: `moonraker.update.conf`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: Z-Mod `ENABLE_PLUGIN`/`DISABLE_PLUGIN` hooks, chroot path, `mod_data/power_on.sh`, Moonraker plugin config.
- Produces: safe service start/stop/restart/status, idempotent autostart block, update-manager registration.

- [ ] **Step 1: Write failing lifecycle tests.**

```python
assert "port_in_use" in init_script
assert "# >>> AD5X WebScreen >>>" in power_on_hook
assert "/usr/prog/app_startup.sh" not in power_on_hook
```

- [ ] **Step 2: Run packaging tests and verify RED.**

Run: `python3 -m unittest tests.test_packaging -v`

Expected before lifecycle implementation: missing files and managed-hook assertions fail.

- [ ] **Step 3: Implement service lifecycle and managed `power_on.sh` block.**

```sh
# >>> AD5X WebScreen >>>
"$AD5X_WEBSCREEN_CONTROL" start >> "$AD5X_WEBSCREEN_BOOT_LOG" 2>&1 &
# <<< AD5X WebScreen <<<
```

The hook must be idempotent, preserve unrelated file content, reject malformed marker state, and support `AD5X_WEBSCREEN_POWER_ON` for isolated tests.

- [ ] **Step 4: Run packaging tests and verify GREEN.**

Run: `python3 -m unittest tests.test_packaging -v`

Expected: all packaging tests pass.

### Task 4: Release verification and live acceptance

**Files:**
- Modify: `README.md` only if observed live behavior differs from documented behavior.
- Verify: all runtime and shell files.

**Interfaces:**
- Consumes: AD5X with Z-Mod and HelixScreen.
- Produces: evidence for install, boot autostart, stream, touch, update, uninstall, and print-load safety.

- [ ] **Step 1: Run the complete offline verification gate.**

Run:

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile webscreen.py
for f in install.sh uninstall.sh update.sh power_on_hook.sh control.sh S71ad5x_webscreen; do sh -n "$f"; done
```

Expected: 0 test failures, Python compilation succeeds, every shell script parses successfully.

- [ ] **Step 2: Install on an idle AD5X and validate lifecycle.**

```sh
./install.sh
./control.sh status
./control.sh token
```

Expected: service listens on port 8010, `/stream` renders the current screen, and remote touch remains disabled until explicitly enabled in Web UI.

- [ ] **Step 3: Reboot and validate Z-Mod user-hook autostart.**

Expected: WebScreen returns on port 8010 without edits to Z-Mod startup sources and `power_on.sh` contains exactly one managed WebScreen block.

- [ ] **Step 4: Validate update and uninstall.**

```sh
./update.sh
./control.sh status
./uninstall.sh
```

Expected: update restarts only WebScreen; uninstall removes only the WebScreen service link, Moonraker include, runtime state (unless KEEP_CONFIG is set), and its managed power-on block.

- [ ] **Step 5: Run print acceptance.**

Open WebScreen during a representative real print with the 10 Hz profile and watch Klipper/Moonraker logs for scheduling/MCU errors, especially E0011 / `timer too close`.

Expected acceptance criterion: print completes with no new Klipper latency/MCU faults attributable to WebScreen. Until this step is observed, the 10 Hz profile remains functionally validated but not print-load-certified.
