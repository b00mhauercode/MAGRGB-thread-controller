"""
Thread API Test
Sends a few solid colors to the MAGRGB via the Nanoleaf Desktop local HTTP API.
Run this to verify the API is working before using the full bridge.

Requirements: Nanoleaf Desktop app must be running.
"""
import json
import time
import urllib.request
import urllib.error

try:
    from config_local import DEVICE_INFO
except ImportError:
    from config import DEVICE_INFO

NANOLEAF_API = "http://127.0.0.1:15765"


def post(endpoint, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{NANOLEAF_API}{endpoint}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        return resp.status, resp.read()


def set_color(hue, sat, bri, label=""):
    status, body = post("/essentials/control", {
        "devices": [DEVICE_INFO],
        "value": {
            "hue":        {"value": hue},
            "sat":        {"value": sat},
            "brightness": {"value": bri},
        },
    })
    print(f"  hsv({hue:3},{sat:3},{bri:3})  {label:<10}  HTTP {status}  {body[:60]}")


print(f"Device: {DEVICE_INFO['defaultName']} ({DEVICE_INFO['id']})")
print(f"API:    {NANOLEAF_API}\n")

# Check service is up
try:
    req = urllib.request.Request(f"{NANOLEAF_API}/essentials/devices", method="GET")
    with urllib.request.urlopen(req, timeout=3) as resp:
        print(f"Service reachable (HTTP {resp.status})\n")
except urllib.error.URLError as e:
    print(f"ERROR: Cannot reach {NANOLEAF_API}: {e}")
    print("Make sure the Nanoleaf Desktop app is running.")
    raise SystemExit(1)

print("Color test — watch the strip:")
set_color(0,   100, 100, "Red")
time.sleep(1.5)
set_color(120, 100, 100, "Green")
time.sleep(1.5)
set_color(240, 100, 100, "Blue")
time.sleep(1.5)
set_color(0,   0,   100, "White")
time.sleep(1.5)
set_color(0,   0,   0,   "Off (bri=0)")
time.sleep(1.0)

print("\nDone.")
