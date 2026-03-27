# ── Your personal device configuration ────────────────────────────────────────
# Copy this file to config_local.py and fill in your values.
# config_local.py is gitignored and will never be committed.
#
# How to find your values:
#   DEVICE_MAC  — run scan.py; look for address with Manufacturer ID 76 (Apple)
#   DEVICE_INFO — grab from Wireshark loopback capture of Nanoleaf Desktop traffic
#                 (filter: tcp.port == 15765), or run discover_thread_device.py
# ──────────────────────────────────────────────────────────────────────────────

DEVICE_MAC = "XX:XX:XX:XX:XX:XX"

# Nanoleaf Thread device descriptor — required by magnus_wled_bridge.py
DEVICE_INFO = {
    "controlVersion": 2,
    "defaultName": "Your Device Name",
    "id": "XXXXXXXXXXX",
    "ip": "xxxx:xxxx:xxxx:x:xxxx:xxxx:xxxx:xxxx",
    "model": "NL62",
    "port": 5683,
    "token": "XXXXXXXXXXXXXXXX",
    "eui64": "xxxxxxxxxxxxxxxx",
}
