"""
Magnus Zone Test
Tests multi-zone STRIPES control via COMMAND_INTERFACE (HAP IID=60).
Cycles through several zone configurations so you can see if per-zone
color control is working on the strip.

Run via magnus_zone_test.bat on the desktop, or:
  python magnus_zone_test.py
"""
import asyncio
import json
import os

from aiohomekit.characteristic_cache import CharacteristicCacheMemory
from aiohomekit.controller.ble.controller import BleController
from aiohomekit.model import Accessories, AccessoriesState

from compat import CompatBleakScanner

try:
    from config_local import DEVICE_MAC
except ImportError:
    from config import DEVICE_MAC

PAIRING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pairing.json")
ALIAS = "magrgb"

AID            = 1
IID_ON         = 51
IID_BRI        = 52
IID_ANIM_WRITE = 60

CMD_DISPLAY_SCENE = bytes([0x07, 0x01])
CMD_DISPLAY_TEMP  = bytes([0x07, 0x08])


# ── TLV2 helpers ───────────────────────────────────────────────────────────────

def _tlv2(tag: int, value: bytes) -> bytes:
    return bytes([tag, len(value)]) + value


def _pack_color(hue: int, sat: int, bri: int) -> bytes:
    i = (hue << 14) | (sat << 7) | bri
    return bytes([(i >> 16) & 0xFF, (i >> 8) & 0xFF, i & 0xFF])


def build_stripes(scene_id: int, colors: list, segment: int,
                  transit_time: int = 255) -> bytes:
    """Build a DISPLAY_SCENE + STRIPES packet for IID=60."""
    meta   = _tlv2(0x01, bytes([scene_id, 0x06, transit_time & 0xFF, 0, segment & 0xFF]))
    pal    = _tlv2(0x02, bytes([len(colors)]) + b"".join(_pack_color(*c) for c in colors))
    inner  = meta + pal
    return CMD_DISPLAY_SCENE + bytes([len(inner) >> 8, len(inner) & 0xFF]) + inner


def build_stream_control(scene_id: int, colors: list) -> bytes:
    """Build a DISPLAY_SCENE + STREAM_CONTROL (0x04) packet — static per-zone colors."""
    meta  = _tlv2(0x01, bytes([scene_id, 0x04]))
    pal   = _tlv2(0x02, bytes([len(colors)]) + b"".join(_pack_color(*c) for c in colors))
    inner = meta + pal
    return CMD_DISPLAY_SCENE + bytes([len(inner) >> 8, len(inner) & 0xFF]) + inner


# ── Main test ──────────────────────────────────────────────────────────────────

CMD_DELETE_SCENE = bytes([0x07, 0x05])


def _tlv2_wrap(tag, value):
    return bytes([tag, len(value)]) + value


async def run_test():
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
            print(f"Found: {pairing.description.name}\n")
            break
    else:
        print("WARNING: device not found — attempting anyway\n")

    # Clear stored scenes so device has no animation to restore
    print("Clearing stored scenes 0-20...")
    for sid in range(0, 21):
        payload = _tlv2_wrap(0x01, bytes([sid]))
        pkt = CMD_DELETE_SCENE + bytes([len(payload) >> 8, len(payload) & 0xFF]) + payload
        try:
            await pairing.put_characteristics([(AID, IID_ANIM_WRITE, pkt)])
            print(f"  Deleted scene {sid}")
        except Exception as e:
            print(f"  Scene {sid}: {e}")
        await asyncio.sleep(0.2)

    # Turn off then on fresh
    print("Resetting strip...")
    await pairing.put_characteristics([(AID, IID_ON, False)])
    await asyncio.sleep(0.5)
    await pairing.put_characteristics([(AID, IID_ON, True)])
    await asyncio.sleep(1.0)
    print("Ready.\n")

    # Test: STRIPES transit_time=0, continuous resend — does resetting animation at frame 0 look static?
    colors_rgb = [(0, 100, 100), (120, 100, 100), (240, 100, 100)]  # Red, Green, Blue
    pkt = build_stripes(scene_id=1, colors=colors_rgb, segment=33, transit_time=0)
    print(f"Test: STRIPES transit_time=0, 50 frames continuous — Red / Green / Blue")
    print(f"  Packet {len(pkt)}b: {pkt.hex()}")
    print("  Streaming 50 frames at 10Hz — watch for static zones...")
    for i in range(50):
        try:
            await pairing.put_characteristics([(AID, IID_ANIM_WRITE, pkt)])
            if i % 10 == 0:
                print(f"  Frame {i+1}/50")
        except Exception as e:
            print(f"  Error at frame {i+1}: {e}")
            break
        await asyncio.sleep(0.1)
    print("  Done.")

    print("\nDone. Turning off.")
    await pairing.put_characteristics([(AID, IID_ON, False)])


if __name__ == "__main__":
    asyncio.run(run_test())
