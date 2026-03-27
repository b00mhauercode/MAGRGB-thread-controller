"""
Clear stored scenes from the MAGRGB device.
Sends DELETE_SCENE for scene IDs 1-10 to remove any saved animations.
Run this once to unstick the device from a persistent animation.
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
IID_ANIM_WRITE = 60

CMD_DELETE_SCENE = bytes([0x07, 0x05])
CMD_DISPLAY_TEMP = bytes([0x07, 0x08])


def _tlv2(tag, value):
    return bytes([tag, len(value)]) + value


def wrap(cmd, payload):
    return cmd + bytes([len(payload) >> 8, len(payload) & 0xFF]) + payload


async def run():
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

    # Delete stored scenes 1-10
    print("Deleting stored scenes 1-10...")
    for scene_id in range(1, 11):
        payload = _tlv2(0x01, bytes([scene_id]))
        pkt = wrap(CMD_DELETE_SCENE, payload)
        try:
            await pairing.put_characteristics([(AID, IID_ANIM_WRITE, pkt)])
            print(f"  Deleted scene {scene_id}")
        except Exception as e:
            print(f"  Scene {scene_id}: {e}")
        await asyncio.sleep(0.3)

    # Turn off
    print("\nTurning off...")
    await pairing.put_characteristics([(AID, IID_ON, False)])
    await asyncio.sleep(1)

    # Turn on and send a solid white via DISPLAY_TEMP to confirm it's working
    print("Sending solid white via DISPLAY_TEMP...")
    await pairing.put_characteristics([(AID, IID_ON, True)])
    await asyncio.sleep(1)

    # 3-zone solid white via DISPLAY_TEMP
    white = [(0, 0, 100)] * 3   # hue=0, sat=0, bri=100 = white
    meta = _tlv2(0x01, bytes([1, 0x06, 24, 0, 33]))
    pal  = _tlv2(0x02, bytes([len(white)]) + b"".join(
        bytes([((h<<14)|(s<<7)|b) >> 16 & 0xFF,
               ((h<<14)|(s<<7)|b) >> 8  & 0xFF,
               ((h<<14)|(s<<7)|b)       & 0xFF])
        for h, s, b in white
    ))
    inner = meta + pal
    pkt = CMD_DISPLAY_TEMP + bytes([len(inner) >> 8, len(inner) & 0xFF]) + inner
    try:
        await pairing.put_characteristics([(AID, IID_ANIM_WRITE, pkt)])
        print("Sent DISPLAY_TEMP solid white — does the strip change?")
    except Exception as e:
        print(f"Error: {e}")

    await asyncio.sleep(5)
    await pairing.put_characteristics([(AID, IID_ON, False)])
    print("Done.")


if __name__ == "__main__":
    asyncio.run(run())
