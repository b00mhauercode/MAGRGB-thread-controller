"""Discover GATT services and characteristics on the MAGRGB"""
import asyncio
from bleak import BleakClient, BleakScanner

try:
    from config_local import DEVICE_MAC
except ImportError:
    from config import DEVICE_MAC

async def main():
    print(f"Connecting to {DEVICE_MAC}...")
    async with BleakClient(DEVICE_MAC) as client:
        print(f"Connected!\n")
        for service in client.services:
            print(f"Service: {service.uuid}  ({service.description})")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  Char: {char.uuid}  handle=0x{char.handle:04x}  [{props}]")
                if "read" in char.properties:
                    try:
                        val = await client.read_gatt_char(char.uuid)
                        print(f"    Value: {val.hex()}  ({val})")
                    except Exception as e:
                        print(f"    Read error: {e}")

asyncio.run(main())
