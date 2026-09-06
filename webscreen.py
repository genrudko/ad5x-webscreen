#!/usr/bin/env python3
from __future__ import annotations

import argparse
import configparser
import fcntl
import json
import math
import os
import platform
import signal
import struct
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any


VERSION = "0.1.0"
DEFAULT_CONFIG_PATH = "/opt/config/mod_data/ad5x_webscreen/webscreen.ini"
DEFAULT_FB_PATH = "/dev/fb0"
DEFAULT_FB_SYSFS = "/sys/class/graphics/fb0"
DEFAULT_INPUT_SYSFS = "/sys/class/input"
DEFAULT_INPUT_DEV = "/dev/input"
FBIOGET_VSCREENINFO = 0x4600

# Linux input-event constants used by the TSC2007 injector.
EV_SYN = 0
EV_KEY = 1
EV_ABS = 3
SYN_REPORT = 0
BTN_TOUCH = 330
ABS_X = 0
ABS_Y = 1
ABS_PRESSURE = 24


@dataclass(frozen=True)
class WebScreenConfig:
    bind: str = "0.0.0.0"
    port: int = 8010
    poll_hz: float = 10.0
    jpeg_quality: int = 75
    nice: int = 10
    touch_allowed: bool = True
    touch_start_enabled: bool = False
    touch_failsafe_s: float = 1.0
    touch_pressure: int = 1200
    helix_settings: str = "/srv/helixscreen/config/settings.json"

    @classmethod
    def defaults(cls) -> "WebScreenConfig":
        return cls()

    @classmethod
    def load(cls, path: str | Path) -> "WebScreenConfig":
        parser = configparser.ConfigParser()
        read = parser.read(path, encoding="utf-8")
        if not read:
            raise FileNotFoundError(path)

        defaults = cls.defaults()
        cfg = cls(
            bind=parser.get("server", "bind", fallback=defaults.bind).strip(),
            port=parser.getint("server", "port", fallback=defaults.port),
            poll_hz=parser.getfloat("server", "poll_hz", fallback=defaults.poll_hz),
            jpeg_quality=parser.getint(
                "server", "jpeg_quality", fallback=defaults.jpeg_quality
            ),
            nice=parser.getint("server", "nice", fallback=defaults.nice),
            touch_allowed=parser.getboolean(
                "touch", "allowed", fallback=defaults.touch_allowed
            ),
            touch_start_enabled=parser.getboolean(
                "touch", "start_enabled", fallback=defaults.touch_start_enabled
            ),
            touch_failsafe_s=parser.getfloat(
                "touch", "failsafe_seconds", fallback=defaults.touch_failsafe_s
            ),
            touch_pressure=parser.getint(
                "touch", "pressure", fallback=defaults.touch_pressure
            ),
            helix_settings=parser.get(
                "touch", "helix_settings", fallback=defaults.helix_settings
            ).strip(),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.bind:
            raise ValueError("server.bind must not be empty")
        if not (1 <= self.port <= 65535):
            raise ValueError("server.port must be between 1 and 65535")
        if not (0.5 <= self.poll_hz <= 30.0):
            raise ValueError("server.poll_hz must be between 0.5 and 30")
        if not (30 <= self.jpeg_quality <= 95):
            raise ValueError("server.jpeg_quality must be between 30 and 95")
        if not (0 <= self.nice <= 19):
            raise ValueError("server.nice must be between 0 and 19")
        if self.touch_start_enabled:
            raise ValueError("touch.start_enabled=true is not allowed; touch must start off")
        if not (0.2 <= self.touch_failsafe_s <= 5.0):
            raise ValueError("touch.failsafe_seconds must be between 0.2 and 5.0")
        if not (1 <= self.touch_pressure <= 4095):
            raise ValueError("touch.pressure must be between 1 and 4095")
        if not self.helix_settings.startswith("/"):
            raise ValueError("touch.helix_settings must be absolute")


@dataclass(frozen=True)
class AxisRange:
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if self.maximum < self.minimum:
            raise ValueError("axis maximum must be >= minimum")

    def clamp(self, value: float | int) -> int:
        rounded = int(round(value))
        return max(self.minimum, min(self.maximum, rounded))

    def __str__(self) -> str:
        return f"{self.minimum}..{self.maximum}"


@dataclass(frozen=True)
class AffineCalibration:
    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    @property
    def determinant(self) -> float:
        return self.a * self.e - self.b * self.d

    def validate(self) -> None:
        values = (self.a, self.b, self.c, self.d, self.e, self.f)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("calibration contains non-finite values")
        if abs(self.determinant) < 1e-6:
            raise ValueError("calibration matrix is not invertible")

    def forward(self, raw_x: float, raw_y: float) -> tuple[float, float]:
        self.validate()
        return (
            self.a * raw_x + self.b * raw_y + self.c,
            self.d * raw_x + self.e * raw_y + self.f,
        )

    def inverse(self, screen_x: float, screen_y: float) -> tuple[float, float]:
        self.validate()
        det = self.determinant
        x = float(screen_x) - self.c
        y = float(screen_y) - self.f
        return (
            (self.e * x - self.b * y) / det,
            (-self.d * x + self.a * y) / det,
        )

    @classmethod
    def from_helix_file(
        cls, path: str | Path
    ) -> tuple["AffineCalibration", str]:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

        calibration = payload.get("input", {}).get("calibration")
        source = "input.calibration"
        if not isinstance(calibration, dict):
            calibration = payload.get("display", {}).get("calibration")
            source = "display.calibration"
        if not isinstance(calibration, dict):
            raise ValueError("Helix touch calibration not found")
        if not bool(calibration.get("valid", True)):
            raise ValueError("Helix touch calibration is marked invalid")

        try:
            result = cls(*(float(calibration[key]) for key in "abcdef"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Helix touch calibration is incomplete") from exc
        result.validate()
        return result, source


@dataclass(frozen=True)
class TouchMapper:
    calibration: AffineCalibration
    x_range: AxisRange
    y_range: AxisRange

    def map_screen(self, screen_x: float, screen_y: float) -> tuple[int, int]:
        raw_x, raw_y = self.calibration.inverse(screen_x, screen_y)
        return self.x_range.clamp(raw_x), self.y_range.clamp(raw_y)


@dataclass(frozen=True)
class FrameGeometry:
    xres: int
    yres: int
    xres_virtual: int
    yres_virtual: int
    xoffset: int
    yoffset: int
    bits_per_pixel: int
    stride: int

    @property
    def bytes_per_pixel(self) -> int:
        if self.bits_per_pixel % 8:
            raise ValueError("bits_per_pixel must be byte-aligned")
        return self.bits_per_pixel // 8

    def validate(self) -> None:
        if self.bits_per_pixel != 32:
            raise ValueError("AD5X WebScreen currently requires a 32-bpp framebuffer")
        if self.xres <= 0 or self.yres <= 0:
            raise ValueError("visible framebuffer dimensions must be positive")
        if self.xoffset < 0 or self.yoffset < 0:
            raise ValueError("framebuffer offsets must be non-negative")
        if self.xoffset + self.xres > self.xres_virtual:
            raise ValueError("visible X range exceeds virtual framebuffer")
        if self.yoffset + self.yres > self.yres_virtual:
            raise ValueError("visible Y range exceeds virtual framebuffer")
        if self.stride < self.xres_virtual * self.bytes_per_pixel:
            raise ValueError("framebuffer stride is smaller than virtual row width")

    def read_plan(self) -> list[tuple[int, int]]:
        self.validate()
        row_bytes = self.xres * self.bytes_per_pixel
        if self.xoffset == 0 and row_bytes == self.stride:
            return [(self.yoffset * self.stride, self.yres * self.stride)]
        return [
            (
                (self.yoffset + row) * self.stride
                + self.xoffset * self.bytes_per_pixel,
                row_bytes,
            )
            for row in range(self.yres)
        ]


@dataclass(frozen=True)
class AbsInfo:
    value: int
    minimum: int
    maximum: int
    fuzz: int
    flat: int
    resolution: int

    @property
    def axis_range(self) -> AxisRange:
        return AxisRange(self.minimum, self.maximum)


def eviocgabs_request(code: int, machine: str) -> int:
    if not 0 <= code <= 0x3F:
        raise ValueError("ABS code out of supported range")
    size = 24
    event_type = ord("E")
    number = 0x40 + code
    if machine.lower().startswith("mips"):
        # MIPS Linux: _IOC_READ=2 and DIRSHIFT=29.
        return (2 << 29) | (size << 16) | (event_type << 8) | number
    # Generic Linux ABI used by x86/arm: _IOC_READ=2 and DIRSHIFT=30.
    return 0x80000000 | (size << 16) | (event_type << 8) | number


def decode_abs_info(raw: bytes) -> AbsInfo:
    if len(raw) < 24:
        raise ValueError("input_absinfo payload must be at least 24 bytes")
    return AbsInfo(*struct.unpack("=6i", raw[:24]))


def pack_input_event(timestamp: float, ev_type: int, code: int, value: int) -> bytes:
    sec = int(timestamp)
    usec = int(round((float(timestamp) - sec) * 1_000_000))
    if usec >= 1_000_000:
        sec += 1
        usec -= 1_000_000
    if usec < 0:
        sec -= 1
        usec += 1_000_000
    # Native long size is intentional: input_event contains timeval with native longs.
    return struct.pack("@llHHi", sec, usec, int(ev_type), int(code), int(value))


def find_touch_device(
    sys_root: str | Path = DEFAULT_INPUT_SYSFS,
    dev_root: str | Path = DEFAULT_INPUT_DEV,
) -> tuple[str | None, str | None]:
    sys_root = Path(sys_root)
    dev_root = Path(dev_root)
    matches: list[tuple[str, str]] = []
    for dev in sorted(dev_root.glob("event*")):
        name_path = sys_root / dev.name / "device" / "name"
        try:
            name = name_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        lname = name.lower()
        if "tsc2007" in lname or "touchscreen" in lname:
            matches.append((str(dev), name))
    if not matches:
        return None, None
    matches.sort(key=lambda pair: (0 if "tsc2007" in pair[1].lower() else 1, pair[0]))
    return matches[0]


def touch_down_events(x: int, y: int, pressure: int) -> list[tuple[int, int, int]]:
    return [
        (EV_KEY, BTN_TOUCH, 1),
        (EV_ABS, ABS_X, int(x)),
        (EV_ABS, ABS_Y, int(y)),
        (EV_ABS, ABS_PRESSURE, int(pressure)),
        (EV_SYN, SYN_REPORT, 0),
    ]


def touch_move_events(x: int, y: int, pressure: int) -> list[tuple[int, int, int]]:
    return [
        (EV_ABS, ABS_X, int(x)),
        (EV_ABS, ABS_Y, int(y)),
        (EV_ABS, ABS_PRESSURE, int(pressure)),
        (EV_SYN, SYN_REPORT, 0),
    ]


def touch_up_events() -> list[tuple[int, int, int]]:
    return [
        (EV_ABS, ABS_PRESSURE, 0),
        (EV_KEY, BTN_TOUCH, 0),
        (EV_SYN, SYN_REPORT, 0),
    ]


def render_index_html() -> bytes:
    return r'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AD5X WebScreen</title>
<style>
html,body{margin:0;background:#111;color:#ddd;font-family:system-ui,sans-serif}
main{max-width:1000px;margin:auto;padding:12px}
h1{font-size:18px;margin:0 0 10px}
#screen{display:block;width:100%;max-width:800px;height:auto;background:#000;user-select:none;-webkit-user-drag:none;touch-action:none}
.controls{display:flex;gap:10px;align-items:center;margin:10px 0;flex-wrap:wrap}
button{padding:7px 12px;font-size:14px}
#armed{font-weight:700}.warn{color:#ffcc66}.ok{color:#7ee787}.muted{opacity:.75}
pre{white-space:pre-wrap;font-size:13px}
</style>
</head>
<body>
<main>
<h1>AD5X WebScreen</h1>
<img id="screen" src="/streams" alt="AD5X screen" draggable="false">
<div class="controls">
<button id="toggle">Enable remote touch</button>
<span id="armed" class="warn">TOUCH DISABLED</span>
</div>
<p class="muted">Remote touch starts disabled. Interrupted gestures are released by the server-side failsafe.</p>
<pre id="stats">loading stats...</pre>
</main>
<script>
const img=document.getElementById('screen');
const toggle=document.getElementById('toggle');
const armed=document.getElementById('armed');
let enabled=false,pointerDown=false,lastMoveTs=0;
function xyFromEvent(ev){const r=img.getBoundingClientRect();const w=img.naturalWidth||800,h=img.naturalHeight||480;const x=Math.max(0,Math.min(w,(ev.clientX-r.left)*w/r.width));const y=Math.max(0,Math.min(h,(ev.clientY-r.top)*h/r.height));return[x,y];}
function api(path,opts={}){return fetch(path,{...opts,cache:'no-store'});}
function paint(v){enabled=v;toggle.textContent=v?'Disable remote touch':'Enable remote touch';armed.textContent=v?'TOUCH ENABLED':'TOUCH DISABLED';armed.className=v?'ok':'warn';}
async function setEnabled(v){const r=await api('/touch/enable?value='+(v?'1':'0'),{method:'POST'});if(!r.ok)throw new Error(await r.text());paint(v);}
toggle.addEventListener('click',()=>setEnabled(!enabled).catch(e=>alert('Touch control failed: '+e.message)));
img.addEventListener('pointerdown',ev=>{if(!enabled)return;ev.preventDefault();pointerDown=true;img.setPointerCapture(ev.pointerId);const[x,y]=xyFromEvent(ev);api(`/touch?e=down&x=${x}&y=${y}`,{method:'POST'});});
img.addEventListener('pointermove',ev=>{if(!enabled||!pointerDown)return;ev.preventDefault();const now=performance.now();if(now-lastMoveTs<25)return;lastMoveTs=now;const[x,y]=xyFromEvent(ev);api(`/touch?e=move&x=${x}&y=${y}`,{method:'POST'});});
async function sendUp(ev){if(!enabled||!pointerDown)return;pointerDown=false;if(ev)ev.preventDefault();await api('/touch?e=up',{method:'POST'});}
img.addEventListener('pointerup',sendUp);img.addEventListener('pointercancel',sendUp);img.addEventListener('lostpointercapture',sendUp);window.addEventListener('blur',()=>{if(pointerDown)sendUp();});document.addEventListener('visibilitychange',()=>{if(document.hidden&&pointerDown)sendUp();});
async function updateStats(){try{const r=await fetch('/stats',{cache:'no-store'});const txt=await r.text();document.getElementById('stats').textContent=txt;const m=/touch_enabled=(true|false)/i.exec(txt);if(m)paint(m[1].toLowerCase()==='true');}catch(e){}}
setInterval(updateStats,1000);updateStats();
</script>
</body>
</html>
'''.encode("utf-8")


def render_iframe_html() -> bytes:
    # Fluidd's iframe webcam loads /screen/stream through Z-Mod nginx. Keep all
    # URLs relative so the same page also works directly at :8010/stream.
    return r'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
html,body{margin:0;padding:0;width:100%;height:100%;background:#000;overflow:hidden}
body{display:flex;align-items:center;justify-content:center}
#screen{display:block;width:100%;height:100%;object-fit:contain;user-select:none;-webkit-user-drag:none;touch-action:none}
</style>
</head>
<body>
<img id="screen" src="streams" alt="AD5X screen" draggable="false">
<script>
const img=document.getElementById('screen');
let pointerDown=false,lastMoveTs=0;
function xyFromEvent(ev){
  const r=img.getBoundingClientRect(),iw=img.naturalWidth||800,ih=img.naturalHeight||480;
  const imageRatio=iw/ih,boxRatio=r.width/r.height;
  let w=r.width,h=r.height,ox=0,oy=0;
  if(boxRatio>imageRatio){w=r.height*imageRatio;ox=(r.width-w)/2;}else{h=r.width/imageRatio;oy=(r.height-h)/2;}
  const x=(ev.clientX-r.left-ox)*iw/w,y=(ev.clientY-r.top-oy)*ih/h;
  return[Math.max(0,Math.min(iw,x)),Math.max(0,Math.min(ih,y))];
}
function api(path){return fetch(path,{method:'POST',cache:'no-store'});}
img.addEventListener('pointerdown',ev=>{ev.preventDefault();pointerDown=true;img.setPointerCapture(ev.pointerId);const[x,y]=xyFromEvent(ev);api(`touch?e=down&x=${x}&y=${y}`);});
img.addEventListener('pointermove',ev=>{if(!pointerDown)return;ev.preventDefault();const now=performance.now();if(now-lastMoveTs<25)return;lastMoveTs=now;const[x,y]=xyFromEvent(ev);api(`touch?e=move&x=${x}&y=${y}`);});
function sendUp(ev){if(!pointerDown)return;pointerDown=false;if(ev)ev.preventDefault();api('touch?e=up');}
img.addEventListener('pointerup',sendUp);img.addEventListener('pointercancel',sendUp);img.addEventListener('lostpointercapture',sendUp);window.addEventListener('blur',()=>sendUp());document.addEventListener('visibilitychange',()=>{if(document.hidden)sendUp();});
</script>
</body>
</html>
'''.encode("utf-8")


class RuntimeState:
    """Shared state and lightweight metrics. Construction has no hardware side effects."""

    def __init__(self, config: WebScreenConfig):
        self.config = config
        self.lock = threading.RLock()
        self.jpeg_cond = threading.Condition(self.lock)
        self.started = time.monotonic()
        self.cpu_started = time.process_time()

        self.clients = 0
        self.seq = 0
        self.jpeg: bytes | None = None
        self.polls = 0
        self.changes = 0
        self.encodes = 0
        self.jpeg_bytes = 0
        self.read_total = 0.0
        self.compare_total = 0.0
        self.encode_total = 0.0
        self.encode_max = 0.0
        self.geometry: FrameGeometry | None = None
        self.last_yoffset: int | None = None
        self.yoffset_changes = 0
        self.last_capture_error = ""

        self.touch_device: str | None = None
        self.touch_name: str | None = None
        self.touch_x_range: AxisRange | None = None
        self.touch_y_range: AxisRange | None = None
        self.touch_pressure_range: AxisRange | None = None
        self.calibration_source: str | None = None
        self.touch_enabled = False
        self.touch_active = False
        self.touch_events = 0
        self.touch_failsafe_releases = 0
        self.touch_last_event = 0.0
        self.touch_last_screen = (0, 0)
        self.touch_last_raw = (0, 0)
        self.touch_error = ""

    def set_geometry(self, geometry: FrameGeometry) -> None:
        with self.lock:
            if self.last_yoffset is not None and geometry.yoffset != self.last_yoffset:
                self.yoffset_changes += 1
            self.last_yoffset = geometry.yoffset
            self.geometry = geometry

    def stats_text(self) -> str:
        with self.lock:
            uptime = time.monotonic() - self.started
            cpu = time.process_time() - self.cpu_started
            cpu_pct = 100.0 * cpu / uptime if uptime > 0 else 0.0
            avg_read = 1000.0 * self.read_total / self.polls if self.polls else 0.0
            avg_compare = (
                1000.0 * self.compare_total / self.polls if self.polls else 0.0
            )
            avg_encode = (
                1000.0 * self.encode_total / self.encodes if self.encodes else 0.0
            )
            avg_jpeg = (
                self.jpeg_bytes / self.encodes / 1024.0 if self.encodes else 0.0
            )
            g = self.geometry
            geom = (
                f"visible={g.xres}x{g.yres} virtual={g.xres_virtual}x{g.yres_virtual} "
                f"xoffset={g.xoffset} yoffset={g.yoffset} bpp={g.bits_per_pixel} stride={g.stride}"
                if g
                else "visible=unknown"
            )
            x_range = str(self.touch_x_range) if self.touch_x_range else "unknown"
            y_range = str(self.touch_y_range) if self.touch_y_range else "unknown"
            p_range = (
                str(self.touch_pressure_range)
                if self.touch_pressure_range
                else "unknown"
            )
            return (
                f"AD5X WebScreen {VERSION}\n"
                f"port={self.config.port} poll_hz={self.config.poll_hz:.1f} quality={self.config.jpeg_quality}\n"
                f"clients={self.clients} uptime={uptime:.1f}s\n"
                f"{geom}\n"
                f"yoffset_changes={self.yoffset_changes}\n"
                f"polls={self.polls} changes={self.changes} encodes={self.encodes}\n"
                f"process_cpu={cpu:.2f}s process_cpu_pct_since_start={cpu_pct:.1f}%\n"
                f"avg_read={avg_read:.2f}ms avg_compare={avg_compare:.3f}ms\n"
                f"avg_encode={avg_encode:.1f}ms max_encode={1000.0*self.encode_max:.1f}ms avg_jpeg={avg_jpeg:.1f}KiB\n"
                f"capture_error={self.last_capture_error or 'none'}\n"
                f"touch_allowed={self.config.touch_allowed} touch_device={self.touch_device or 'none'} name={self.touch_name or 'none'}\n"
                f"touch_ranges=x:{x_range} y:{y_range} pressure:{p_range} calibration={self.calibration_source or 'none'}\n"
                f"touch_enabled={self.touch_enabled} touch_active={self.touch_active} touch_events={self.touch_events} "
                f"failsafe_releases={self.touch_failsafe_releases}\n"
                f"touch_last_screen={self.touch_last_screen} touch_last_raw={self.touch_last_raw}\n"
                f"touch_error={self.touch_error or 'none'}\n"
            )


class FramebufferDevice:
    def __init__(
        self,
        path: str = DEFAULT_FB_PATH,
        sysfs_root: str | Path = DEFAULT_FB_SYSFS,
    ):
        self.path = path
        self.sysfs_root = Path(sysfs_root)
        self.fd: int | None = None

    def open(self) -> None:
        if self.fd is None:
            self.fd = os.open(self.path, os.O_RDONLY)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _require_fd(self) -> int:
        if self.fd is None:
            raise RuntimeError("framebuffer is not open")
        return self.fd

    def geometry(self) -> FrameGeometry:
        fd = self._require_fd()
        buf = bytearray(160)
        fcntl.ioctl(fd, FBIOGET_VSCREENINFO, buf, True)
        xres, yres, xvirt, yvirt, xoff, yoff, bpp = struct.unpack_from(
            "=7I", buf, 0
        )
        try:
            stride = int((self.sysfs_root / "stride").read_text().strip())
        except (OSError, ValueError):
            stride = xvirt * (bpp // 8)
        geometry = FrameGeometry(
            xres=xres,
            yres=yres,
            xres_virtual=xvirt,
            yres_virtual=yvirt,
            xoffset=xoff,
            yoffset=yoff,
            bits_per_pixel=bpp,
            stride=stride,
        )
        geometry.validate()
        return geometry

    def read_visible(self) -> tuple[bytes, FrameGeometry]:
        fd = self._require_fd()
        geometry = self.geometry()
        chunks: list[bytes] = []
        for offset, size in geometry.read_plan():
            os.lseek(fd, offset, os.SEEK_SET)
            remaining = size
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    raise RuntimeError("short read from framebuffer")
                chunks.append(chunk)
                remaining -= len(chunk)
        raw = b"".join(chunks)
        expected = geometry.xres * geometry.yres * geometry.bytes_per_pixel
        if len(raw) != expected:
            raise RuntimeError(
                f"visible framebuffer size mismatch: got {len(raw)}, expected {expected}"
            )
        return raw, geometry


class TouchController:
    def __init__(self, config: WebScreenConfig, state: RuntimeState):
        self.config = config
        self.state = state
        self.lock = threading.RLock()
        self.fd: int | None = None
        self.mapper: TouchMapper | None = None
        self.pressure = config.touch_pressure
        self.stop_event = threading.Event()
        self.failsafe_thread: threading.Thread | None = None

    def _query_abs_info(self, device: str, code: int) -> AbsInfo:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        try:
            buf = bytearray(24)
            request = eviocgabs_request(code, platform.machine())
            fcntl.ioctl(fd, request, buf, True)
            return decode_abs_info(buf)
        finally:
            os.close(fd)

    def probe(self, require_calibration: bool = False) -> None:
        if not self.config.touch_allowed:
            return
        dev, name = find_touch_device()
        if not dev:
            raise RuntimeError("TSC2007/Touchscreen input device not found")
        x_info = self._query_abs_info(dev, ABS_X)
        y_info = self._query_abs_info(dev, ABS_Y)
        p_info = self._query_abs_info(dev, ABS_PRESSURE)

        calibration: AffineCalibration | None = None
        source: str | None = None
        try:
            calibration, source = AffineCalibration.from_helix_file(
                self.config.helix_settings
            )
        except Exception:
            if require_calibration:
                raise

        with self.state.lock:
            self.state.touch_device = dev
            self.state.touch_name = name
            self.state.touch_x_range = x_info.axis_range
            self.state.touch_y_range = y_info.axis_range
            self.state.touch_pressure_range = p_info.axis_range
            self.state.calibration_source = source

        if calibration is not None:
            self.mapper = TouchMapper(
                calibration, x_info.axis_range, y_info.axis_range
            )
        self.pressure = p_info.axis_range.clamp(self.config.touch_pressure)

    def _open_writer(self) -> None:
        if self.fd is not None:
            return
        dev = self.state.touch_device
        if not dev:
            raise RuntimeError("touch device has not been probed")
        self.fd = os.open(dev, os.O_WRONLY | os.O_NONBLOCK)

    def _close_writer(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None

    def _write_events(self, events: list[tuple[int, int, int]]) -> None:
        self._open_writer()
        assert self.fd is not None
        now = time.time()
        payload = b"".join(
            pack_input_event(now, ev_type, code, value)
            for ev_type, code, value in events
        )
        try:
            os.write(self.fd, payload)
        except OSError:
            self._close_writer()
            self._open_writer()
            assert self.fd is not None
            os.write(self.fd, payload)

    def _ensure_failsafe(self) -> None:
        if self.failsafe_thread and self.failsafe_thread.is_alive():
            return
        self.stop_event.clear()
        self.failsafe_thread = threading.Thread(
            target=self._failsafe_loop,
            name="webscreen-touch-failsafe",
            daemon=True,
        )
        self.failsafe_thread.start()

    def enable(self) -> None:
        if not self.config.touch_allowed:
            raise RuntimeError("remote touch is disabled by configuration")
        with self.lock:
            self.probe(require_calibration=True)
            if self.mapper is None:
                raise RuntimeError("valid Helix calibration is required for remote touch")
            self._open_writer()
            # Clear any stale touch state left by an interrupted prior process.
            self._write_events(touch_up_events())
            with self.state.lock:
                self.state.touch_enabled = True
                self.state.touch_active = False
                self.state.touch_error = ""
            self._ensure_failsafe()

    def disable(self) -> None:
        with self.lock:
            try:
                if self.fd is not None:
                    self._write_events(touch_up_events())
            except Exception as exc:
                with self.state.lock:
                    self.state.touch_error = str(exc)
            finally:
                with self.state.lock:
                    self.state.touch_active = False
                    self.state.touch_enabled = False
                self._close_writer()

    def _map(self, screen_x: float, screen_y: float) -> tuple[int, int]:
        if self.mapper is None:
            raise RuntimeError("touch mapper is not initialized")
        return self.mapper.map_screen(screen_x, screen_y)

    def down(self, screen_x: float, screen_y: float) -> None:
        with self.lock:
            if not self.state.touch_enabled:
                raise RuntimeError("remote touch is not enabled")
            raw_x, raw_y = self._map(screen_x, screen_y)
            self._write_events(touch_down_events(raw_x, raw_y, self.pressure))
            now = time.monotonic()
            with self.state.lock:
                self.state.touch_active = True
                self.state.touch_last_event = now
                self.state.touch_events += 1
                self.state.touch_last_screen = (
                    int(round(screen_x)),
                    int(round(screen_y)),
                )
                self.state.touch_last_raw = (raw_x, raw_y)

    def move(self, screen_x: float, screen_y: float) -> None:
        with self.lock:
            if not self.state.touch_enabled or not self.state.touch_active:
                return
            raw_x, raw_y = self._map(screen_x, screen_y)
            self._write_events(touch_move_events(raw_x, raw_y, self.pressure))
            now = time.monotonic()
            with self.state.lock:
                self.state.touch_last_event = now
                self.state.touch_events += 1
                self.state.touch_last_screen = (
                    int(round(screen_x)),
                    int(round(screen_y)),
                )
                self.state.touch_last_raw = (raw_x, raw_y)

    def up(self) -> None:
        with self.lock:
            if not self.state.touch_active:
                return
            self._write_events(touch_up_events())
            with self.state.lock:
                self.state.touch_active = False
                self.state.touch_last_event = time.monotonic()
                self.state.touch_events += 1

    def _failsafe_loop(self) -> None:
        while not self.stop_event.wait(0.1):
            release = False
            with self.state.lock:
                release = (
                    self.state.touch_enabled
                    and self.state.touch_active
                    and time.monotonic() - self.state.touch_last_event
                    > self.config.touch_failsafe_s
                )
            if release:
                try:
                    self.up()
                    with self.state.lock:
                        self.state.touch_failsafe_releases += 1
                except Exception as exc:
                    with self.state.lock:
                        self.state.touch_error = str(exc)

    def shutdown(self) -> None:
        self.stop_event.set()
        self.disable()
        if self.failsafe_thread and self.failsafe_thread.is_alive():
            self.failsafe_thread.join(timeout=1.0)


class CaptureEngine:
    def __init__(
        self,
        config: WebScreenConfig,
        state: RuntimeState,
        framebuffer: FramebufferDevice,
    ):
        self.config = config
        self.state = state
        self.framebuffer = framebuffer
        self.stop_event = threading.Event()
        self.client_event = threading.Event()
        self.capture_lock = threading.Lock()
        self.thread: threading.Thread | None = None

    @staticmethod
    def _encode_jpeg(raw: bytes, geometry: FrameGeometry, quality: int) -> bytes:
        from PIL import Image

        image = Image.frombytes(
            "RGBA",
            (geometry.xres, geometry.yres),
            raw,
            "raw",
            "BGRA",
        ).convert("RGB")
        import io

        out = io.BytesIO()
        image.save(out, format="JPEG", quality=quality)
        return out.getvalue()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._loop,
            name="webscreen-capture",
            daemon=True,
        )
        self.thread.start()

    def add_client(self) -> None:
        with self.state.lock:
            self.state.clients += 1
            self.client_event.set()

    def remove_client(self) -> None:
        with self.state.lock:
            self.state.clients = max(0, self.state.clients - 1)
            if self.state.clients == 0:
                self.client_event.clear()

    def _capture_raw(self) -> tuple[bytes, FrameGeometry, float]:
        with self.capture_lock:
            t0 = time.monotonic()
            raw, geometry = self.framebuffer.read_visible()
            return raw, geometry, time.monotonic() - t0

    def snapshot(self) -> bytes:
        raw, geometry, read_dt = self._capture_raw()
        e0 = time.monotonic()
        jpeg = self._encode_jpeg(raw, geometry, self.config.jpeg_quality)
        enc_dt = time.monotonic() - e0
        with self.state.jpeg_cond:
            self.state.set_geometry(geometry)
            self.state.read_total += read_dt
            self.state.polls += 1
            self.state.changes += 1
            self.state.encodes += 1
            self.state.encode_total += enc_dt
            self.state.encode_max = max(self.state.encode_max, enc_dt)
            self.state.jpeg_bytes += len(jpeg)
            self.state.jpeg = jpeg
            self.state.seq += 1
            self.state.jpeg_cond.notify_all()
        return jpeg

    def _loop(self) -> None:
        period = 1.0 / self.config.poll_hz
        while not self.stop_event.is_set():
            if not self.client_event.wait(timeout=0.25):
                continue
            prev: bytes | None = None
            next_deadline = time.monotonic()

            while self.client_event.is_set() and not self.stop_event.is_set():
                try:
                    raw, geometry, read_dt = self._capture_raw()
                    c0 = time.monotonic()
                    changed = prev is None or raw != prev
                    cmp_dt = time.monotonic() - c0
                    jpeg: bytes | None = None
                    enc_dt = 0.0
                    if changed:
                        e0 = time.monotonic()
                        jpeg = self._encode_jpeg(
                            raw, geometry, self.config.jpeg_quality
                        )
                        enc_dt = time.monotonic() - e0

                    with self.state.jpeg_cond:
                        self.state.set_geometry(geometry)
                        self.state.polls += 1
                        self.state.read_total += read_dt
                        self.state.compare_total += cmp_dt
                        if changed and jpeg is not None:
                            self.state.changes += 1
                            self.state.encodes += 1
                            self.state.encode_total += enc_dt
                            self.state.encode_max = max(
                                self.state.encode_max, enc_dt
                            )
                            self.state.jpeg_bytes += len(jpeg)
                            self.state.jpeg = jpeg
                            self.state.seq += 1
                            self.state.jpeg_cond.notify_all()
                        self.state.last_capture_error = ""
                    prev = raw
                except Exception as exc:
                    with self.state.lock:
                        self.state.last_capture_error = str(exc)
                    time.sleep(0.25)

                next_deadline += period
                delay = next_deadline - time.monotonic()
                if delay > 0:
                    self.stop_event.wait(delay)
                else:
                    next_deadline = time.monotonic()

    def shutdown(self) -> None:
        self.stop_event.set()
        self.client_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)


class WebScreenService:
    def __init__(self, config: WebScreenConfig):
        self.config = config
        self.state = RuntimeState(config)
        self.framebuffer = FramebufferDevice()
        self.touch = TouchController(config, self.state)
        self.capture = CaptureEngine(config, self.state, self.framebuffer)
        self.httpd: WebScreenHTTPServer | None = None

    def probe(self, require_touch: bool = False) -> None:
        self.framebuffer.open()
        geometry = self.framebuffer.geometry()
        self.state.set_geometry(geometry)
        if self.config.touch_allowed:
            try:
                self.touch.probe(require_calibration=require_touch)
                with self.state.lock:
                    self.state.touch_error = ""
            except Exception as exc:
                with self.state.lock:
                    self.state.touch_error = str(exc)
                if require_touch:
                    raise

    def clamp_screen_coords(self, x: float, y: float) -> tuple[float, float]:
        with self.state.lock:
            g = self.state.geometry
        if g is None:
            return x, y
        return (
            max(0.0, min(float(g.xres), float(x))),
            max(0.0, min(float(g.yres), float(y))),
        )

    def run(self) -> None:
        self.probe(require_touch=False)
        self.capture.start()
        self.httpd = WebScreenHTTPServer(
            (self.config.bind, self.config.port), WebScreenRequestHandler, self
        )

        g = self.state.geometry
        print(
            f"Detected fb0: visible={g.xres}x{g.yres} "
            f"virtual={g.xres_virtual}x{g.yres_virtual} "
            f"xoffset={g.xoffset} yoffset={g.yoffset} "
            f"bpp={g.bits_per_pixel} stride={g.stride}",
            flush=True,
        )
        if self.config.touch_allowed:
            print(
                f"Detected touch: {self.state.touch_device or 'NONE'} "
                f"({self.state.touch_name or 'NONE'}) calibration={self.state.calibration_source or 'NONE'}",
                flush=True,
            )
        print(
            f"AD5X WebScreen {VERSION} listening on {self.config.bind}:{self.config.port} "
            f"(poll {self.config.poll_hz:.1f} Hz, JPEG quality {self.config.jpeg_quality})",
            flush=True,
        )
        print("Remote touch starts DISABLED.", flush=True)

        try:
            self.httpd.serve_forever(poll_interval=0.2)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        try:
            self.touch.shutdown()
        finally:
            self.capture.shutdown()
            self.framebuffer.close()
            if self.httpd is not None:
                try:
                    self.httpd.server_close()
                except Exception:
                    pass


class WebScreenHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        app: WebScreenService,
    ):
        self.app = app
        super().__init__(server_address, handler_cls)


class WebScreenRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: WebScreenHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_bytes(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _parsed(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query)


    def do_GET(self) -> None:
        path, _query = self._parsed()
        app = self.server.app

        if path == "/":
            self._send_bytes(200, "text/html; charset=utf-8", render_index_html())
            return
        if path == "/stream":
            self._send_bytes(200, "text/html; charset=utf-8", render_iframe_html())
            return
        if path == "/health":
            self._send_bytes(200, "application/json", b'{"status":"ok"}\n')
            return
        if path == "/stats":
            self._send_bytes(
                200,
                "text/plain; charset=utf-8",
                app.state.stats_text().encode("utf-8"),
            )
            return
        if path == "/snapshot":
            try:
                jpeg = app.capture.snapshot()
            except Exception as exc:
                self._send_bytes(
                    500,
                    "text/plain; charset=utf-8",
                    (f"snapshot failed: {exc}\n").encode("utf-8"),
                )
                return
            self._send_bytes(200, "image/jpeg", jpeg)
            return
        if path != "/streams":
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()

        app.capture.add_client()
        last_seq = -1
        try:
            while True:
                with app.state.jpeg_cond:
                    app.state.jpeg_cond.wait_for(
                        lambda: app.state.seq != last_seq,
                        timeout=5.0,
                    )
                    if app.state.seq == last_seq or app.state.jpeg is None:
                        continue
                    last_seq = app.state.seq
                    jpeg = app.state.jpeg
                header = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + (f"Content-Length: {len(jpeg)}\r\n\r\n").encode("ascii")
                )
                self.wfile.write(header)
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            app.capture.remove_client()

    def do_POST(self) -> None:
        path, query = self._parsed()
        app = self.server.app

        if path not in ("/touch/enable", "/touch"):
            self.send_error(404)
            return
        if not app.config.touch_allowed:
            self._send_bytes(403, "text/plain; charset=utf-8", b"remote touch disabled\n")
            return

        try:
            if path == "/touch/enable":
                value = query.get("value", ["0"])[0]
                if value == "1":
                    app.touch.enable()
                elif value == "0":
                    app.touch.disable()
                else:
                    raise ValueError("value must be 0 or 1")
                self._send_bytes(
                    200,
                    "text/plain; charset=utf-8",
                    f"touch_enabled={app.state.touch_enabled}\n".encode("ascii"),
                )
                return

            event = query.get("e", [""])[0]
            with app.state.lock:
                enabled = app.state.touch_enabled
            # Fluidd's SCREEN iframe is directly interactive: first DOWN arms
            # touch automatically. Process start/reboot still resets it to OFF.
            if not enabled:
                if event == "down":
                    app.touch.enable()
                elif event == "up":
                    self._send_bytes(200, "text/plain; charset=utf-8", b"ok\n")
                    return
                else:
                    self._send_bytes(403, "text/plain; charset=utf-8", b"remote touch is not enabled\n")
                    return

            if event == "up":
                app.touch.up()
            elif event in ("down", "move"):
                x = float(query.get("x", ["nan"])[0])
                y = float(query.get("y", ["nan"])[0])
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError("finite x and y are required")
                x, y = app.clamp_screen_coords(x, y)
                if event == "down":
                    app.touch.down(x, y)
                else:
                    app.touch.move(x, y)
            else:
                raise ValueError("unknown touch event")
            self._send_bytes(200, "text/plain; charset=utf-8", b"ok\n")
        except Exception as exc:
            with app.state.lock:
                app.state.touch_error = str(exc)
            self._send_bytes(
                500,
                "text/plain; charset=utf-8",
                (f"touch failed: {exc}\n").encode("utf-8"),
            )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AD5X WebScreen")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--check",
        action="store_true",
        help="probe framebuffer/touch and exit without opening the HTTP port",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = WebScreenConfig.load(args.config)
    service = WebScreenService(config)

    if args.check:
        try:
            service.probe(require_touch=config.touch_allowed)
            g = service.state.geometry
            print(
                f"OK fb={g.xres}x{g.yres} virtual={g.xres_virtual}x{g.yres_virtual} "
                f"stride={g.stride} touch={service.state.touch_device or 'disabled'} "
                f"calibration={service.state.calibration_source or 'none'}"
            )
            return 0
        finally:
            service.shutdown()

    def stop_from_signal(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_from_signal)
    signal.signal(signal.SIGINT, stop_from_signal)

    try:
        service.run()
    except KeyboardInterrupt:
        service.shutdown()
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
