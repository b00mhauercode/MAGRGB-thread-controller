"""
Magnus HAP-BLE STRIPES Bridge
Presents the Secretlab MAGRGB as a WLED device for SignalRGB, forwarding
colors via the HAP-BLE STRIPES animation protocol (IID=60) with 10 zones.

10 zones gives a good balance: visible color variation without the strip
looking like a blur. Each zone = 10% of the strip (segment=10).

Requirements:
  - Device must be paired (run pair.py first, produces pairing.json)
  - config_local.py must have DEVICE_MAC set

Usage:
  Run as Administrator:  python magnus_hapble_bridge.py
  (Port 80 requires admin on Windows)

In SignalRGB:
  Home -> Lighting Services -> WLED -> "Discover WLED device by IP" -> 127.0.0.2
"""
import asyncio
import colorsys
import json
import os
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from aiohomekit.characteristic_cache import CharacteristicCacheMemory
from aiohomekit.controller.ble.controller import BleController
from aiohomekit.model import Accessories, AccessoriesState

from compat import CompatBleakScanner

try:
    from config_local import DEVICE_MAC
except ImportError:
    from config import DEVICE_MAC

# ── Configuration ──────────────────────────────────────────────────────────────

PAIRING_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pairing.json")
ALIAS         = "magrgb"
WLED_MAC      = DEVICE_MAC.replace(":", "")

# HAP characteristic IIDs
AID            = 1
IID_ON         = 51
IID_ANIM_WRITE = 60

# Animation config — 10 zones, each 10% of the strip
NUM_ZONES     = 10
ZONE_SEGMENT  = 10     # % of strip per zone (10 × 10 = 100%)
TRANSIT_TIME  = 5      # fast transition
SCENE_ID      = 1      # toggles between 1 and 2 each frame

CMD_DISPLAY_SCENE = bytes([0x07, 0x01])
CMD_DELETE_SCENE  = bytes([0x07, 0x05])

HTTP_PORT     = 80
UDP_PORT      = 21325
SEND_INTERVAL = 0.1    # 10 Hz — HAP-BLE round-trip limit

# ──────────────────────────────────────────────────────────────────────────────


# ── Shared color state ─────────────────────────────────────────────────────────

_lock          = threading.Lock()
_pending_zones = None
_global_bri    = 100
_udp_count     = 0


def set_zones(zones):
    global _pending_zones
    with _lock:
        _pending_zones = zones


def set_brightness(bri_0_100):
    global _global_bri
    with _lock:
        _global_bri = max(0, min(100, bri_0_100))


# ── WLED JSON responses ────────────────────────────────────────────────────────

WLED_INFO = {
    "ver": "0.14.0", "vid": 2310130,
    "leds": {"count": 123, "pwr": 0, "fps": 30, "maxpwr": 5, "maxseg": 32,
             "seglc": [123], "lc": 123, "rgbw": False, "wv": 0, "cct": 0},
    "str": False, "name": "Magnus RGB Strip (HAP-BLE)", "udpport": UDP_PORT,
    "live": False, "lm": "", "lip": "", "ws": 0,
    "fxcount": 118, "palcount": 71, "cpalcount": 0,
    "wifi": {"bssid": "00:00:00:00:00:00", "rssi": -50, "signal": 100, "channel": 1},
    "fs": {"u": 0, "t": 0, "pj": 0}, "ndc": 0,
    "arch": "esp32", "core": "v3.3.6", "lwip": 2,
    "freeheap": 100000, "uptime": 1000, "opt": 131,
    "brand": "WLED", "product": "FOSS",
    "mac": WLED_MAC, "ip": "127.0.0.2",
}

WLED_STATE = {
    "on": True, "bri": 255, "transition": 7, "ps": -1, "pl": -1,
    "nl": {"on": False, "dur": 60, "mode": 1, "tbri": 0, "rem": -1},
    "udpn": {"send": False, "recv": False, "sgrp": 0, "rgrp": 0},
    "lor": 0, "mainseg": 0,
    "seg": [{"id": 0, "start": 0, "stop": 123, "len": 123,
             "grp": 1, "spc": 0, "of": 0, "on": True, "frz": False,
             "bri": 255, "cct": 127, "set": 0,
             "col": [[255, 255, 255, 0], [0, 0, 0, 0], [0, 0, 0, 0]],
             "fx": 0, "sx": 128, "ix": 128, "pal": 0,
             "c1": 128, "c2": 128, "c3": 16,
             "sel": True, "rev": False, "mi": False,
             "o1": False, "o2": False, "o3": False, "si": 0, "m12": 0}],
}


# ── HTTP handler ───────────────────────────────────────────────────────────────

class WLEDHttpHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path in ("/json/info", "/json"):
            body = json.dumps({"state": WLED_STATE, "info": WLED_INFO} if path == "/json"
                              else WLED_INFO).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/json/state":
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length > 4096:
                self.send_response(413); self.end_headers(); return
            try:
                data = json.loads(self.rfile.read(length))
                if "bri" in data:
                    set_brightness(round(data["bri"] / 255 * 100))
                    WLED_STATE["bri"] = data["bri"]
                if data.get("on") is False:
                    set_zones([(0, 0, 0)] * NUM_ZONES)
            except Exception as e:
                print(f"  HTTP POST parse error: {e}")
            resp = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, fmt, *args):
        print(f"  HTTP {args[0]} {args[1]}")


# ── WLED UDP handler ───────────────────────────────────────────────────────────

class WLEDUdpHandler(socketserver.BaseRequestHandler):
    def handle(self):
        global _udp_count
        data = self.request[0]
        if len(data) < 7:
            return
        if data[0] == 0x04:
            n = min(123, (len(data) - 4) // 3)
            if n > 0:
                zones = []
                for z in range(NUM_ZONES):
                    lo = int(z * n / NUM_ZONES)
                    hi = max(lo + 1, int((z + 1) * n / NUM_ZONES))
                    hi = min(hi, n)
                    count = hi - lo
                    r = round(sum(data[4 + (lo+i)*3]   for i in range(count)) / count)
                    g = round(sum(data[4 + (lo+i)*3+1] for i in range(count)) / count)
                    b = round(sum(data[4 + (lo+i)*3+2] for i in range(count)) / count)
                    zones.append((r, g, b))
                _udp_count += 1
                if _udp_count % 30 == 1:
                    r0, g0, b0 = zones[0]
                    print(f"  UDP frame #{_udp_count}  z0=rgb({r0},{g0},{b0})")
                set_zones(zones)
        else:
            print(f"  UDP unknown protocol byte: 0x{data[0]:02x} (len={len(data)})")


# ── HAP-BLE animation helpers ──────────────────────────────────────────────────

def rgb_to_hapsv(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return round(h * 360), round(s * 100), round(v * 100)


def _tlv2(tag, value):
    return bytes([tag, len(value)]) + value


def _pack_color(hue, sat, bri):
    i = (hue << 14) | (sat << 7) | bri
    return bytes([(i >> 16) & 0xFF, (i >> 8) & 0xFF, i & 0xFF])


def build_delete_scene(scene_id):
    payload = _tlv2(0x01, bytes([scene_id]))
    return bytes([0x07, 0x05]) + bytes([len(payload) >> 8, len(payload) & 0xFF]) + payload


def build_stripes(scene_id, colors):
    meta  = _tlv2(0x01, bytes([scene_id, 0x06, TRANSIT_TIME, 0, ZONE_SEGMENT]))
    pal   = _tlv2(0x02, bytes([len(colors)]) + b"".join(_pack_color(*c) for c in colors))
    inner = meta + pal
    return CMD_DISPLAY_SCENE + bytes([len(inner) >> 8, len(inner) & 0xFF]) + inner


# ── HAP-BLE loop ───────────────────────────────────────────────────────────────

async def hap_loop():
    with open(PAIRING_FILE) as f:
        pairing_data = json.load(f)[ALIAS]

    scanner    = CompatBleakScanner()
    controller = BleController(
        char_cache=CharacteristicCacheMemory(),
        bleak_scanner_instance=scanner,
    )
    await controller.async_start()

    pairing = controller.load_pairing(ALIAS, pairing_data)
    pairing._accessories_state = AccessoriesState(Accessories(), 0, None, 0)

    print(f"Waiting for {DEVICE_MAC}...")
    for _ in range(30):
        await asyncio.sleep(1)
        if pairing.description is not None:
            print(f"Found: {pairing.description.name}")
            break
    else:
        print("WARNING: Device not found, attempting anyway...")

    # Clear stored scenes and do a fresh start
    print("Clearing stored scenes...")
    for sid in range(1, 11):
        try:
            await pairing.put_characteristics([(AID, IID_ANIM_WRITE, build_delete_scene(sid))])
        except Exception:
            pass
        await asyncio.sleep(0.2)

    print("Startup: turning on...")
    await pairing.put_characteristics([(AID, IID_ON, False)])
    await asyncio.sleep(0.5)
    await pairing.put_characteristics([(AID, IID_ON, True)])
    await asyncio.sleep(1.0)
    print("Ready.\n")

    last_time    = 0.0
    last_sent    = None
    scene_toggle = 1

    print("HAP-BLE loop running.\n")

    while True:
        try:
            now = asyncio.get_running_loop().time()
            with _lock:
                zones = _pending_zones
                bri   = _global_bri

            if zones is not None and (now - last_time) >= SEND_INTERVAL:
                is_off = all(r == 0 and g == 0 and b == 0 for r, g, b in zones)

                if is_off:
                    try:
                        await pairing.put_characteristics([(AID, IID_ON, False)])
                        last_time = now
                        last_sent = None
                        print("  HAP -> OFF")
                    except Exception as e:
                        print(f"  HAP write error: {e}")
                        await asyncio.sleep(2)
                else:
                    colors = []
                    for r, g, b in zones:
                        h, s, v = rgb_to_hapsv(r, g, b)
                        colors.append((h, s, round(v * bri / 100)))

                    if colors != last_sent:
                        try:
                            pkt = build_stripes(scene_toggle, colors)
                            await pairing.put_characteristics([(AID, IID_ANIM_WRITE, pkt)])
                            scene_toggle = 2 if scene_toggle == 1 else 1
                            last_time = now
                            last_sent = colors
                            h0, s0, v0 = colors[0]
                            print(f"  HAP -> {NUM_ZONES} zones  z0=hsv({h0},{s0},{v0})")
                        except Exception as e:
                            print(f"  HAP write error: {e}")
                            await asyncio.sleep(2)

        except Exception as e:
            print(f"HAP loop error: {e}")
            await asyncio.sleep(2)

        await asyncio.sleep(0.02)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    udp_server = socketserver.UDPServer(("127.0.0.2", UDP_PORT), WLEDUdpHandler)
    threading.Thread(target=udp_server.serve_forever, daemon=True).start()
    print(f"WLED UDP on 127.0.0.2:{UDP_PORT}")

    try:
        http_server = HTTPServer(("127.0.0.2", HTTP_PORT), WLEDHttpHandler)
        threading.Thread(target=http_server.serve_forever, daemon=True).start()
        print(f"WLED HTTP on :{HTTP_PORT}")
    except PermissionError:
        user = os.environ.get("USERNAME", "Everyone")
        print(f"\nERROR: Port 80 requires Administrator.")
        print(f"  Option A: Right-click terminal -> 'Run as administrator'")
        print(f"  Option B (one-time):")
        print(f"    netsh http add urlacl url=http://127.0.0.2:80/ user={user}")
        sys.exit(1)

    print()
    print("In SignalRGB: Lighting Services -> WLED -> IP: 127.0.0.2")
    print("Ctrl+C to quit\n")

    try:
        asyncio.run(hap_loop())
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        udp_server.shutdown()
        http_server.shutdown()


if __name__ == "__main__":
    main()
