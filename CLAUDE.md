# CLAUDE.md

## Project Overview

A Python bridge that exposes a **Secretlab Magnus XL RGB desk strip** (MAGRGB) to **SignalRGB** by emulating a WLED device. The hardware uses the **HAP-BLE** (Apple HomeKit Accessory Protocol over Bluetooth LE) protocol, co-developed with Nanoleaf.

SignalRGB has a built-in WLED integration that streams colors over UDP/HTTP to 127.0.0.2. This bridge receives those streams, converts RGB to HSV, and writes the values to the strip via encrypted HAP-BLE GATT characteristics.

## Quick Setup

### Prerequisites

- Python 3.9+ (tested on 3.11)
- Windows 10/11 with a Bluetooth LE adapter
- Device factory-reset (hold reset ~10s until light flashes)

```bash
pip install -r requirements.txt
```

### 1. Configure your device MAC

Copy `config.py` to `config_local.py` and fill in your device's BLE MAC address:

```python
DEVICE_MAC = "XX:XX:XX:XX:XX:XX"   # Apple manufacturer ID address
```

Find it with:

```bash
python scan.py          # lists all nearby BLE devices
python scan_adv.py      # shows raw advertisement data — look for Manufacturer ID 76 (Apple)
```

### 2. Pair once (saves pairing.json)

```bash
python pair.py XXX-XX-XXX
```

Pass the 8-digit HomeKit setup code from the device label. Format must be `XXX-XX-XXX`.

### 3. Test

```bash
python test.py
```

The strip should cycle: RED → GREEN → BLUE → WHITE 50% → OFF.

### 4. Run the bridge (requires Administrator for port 80)

```bash
python magnus_wled_bridge.py
```

### 5. Add to SignalRGB

Home → Lighting Services → WLED → "Discover WLED device by IP" → `127.0.0.2` → Enter → Link "Magnus RGB Strip".

## Key Dependencies

| Package | Version | Purpose |
|---|---|---|
| `bleak` | >=0.21.0 | Bluetooth LE scanning and GATT connections |
| `aiohomekit` | >=3.1.0 | HAP-BLE pairing and encrypted characteristic writes |

## Project Structure

| File | Purpose |
|---|---|
| `magnus_wled_bridge.py` | **Main script** — WLED emulator + HAP-BLE bridge for SignalRGB |
| `pair.py` | One-time HAP-BLE SRP pairing, writes `pairing.json` |
| `test.py` | Color cycle test — verifies control works after pairing |
| `scan.py` | BLE scanner — lists nearby devices with names and MACs |
| `scan_adv.py` | Advertisement scanner — shows raw manufacturer data |
| `discover_services.py` | GATT enumerator — lists all services and characteristics |
| `compat.py` | Bleak 2.x compatibility shim for aiohomekit's `BleController` |
| `config.py` | Template for device config — copy to `config_local.py` |
| `config_local.py` | Personal device config (gitignored) — set `DEVICE_MAC` here |
| `pairing.json` | Long-term HAP keypair written by `pair.py` (gitignored) |
| `requirements.txt` | Python dependencies |

## Architecture

```
SignalRGB                magnus_wled_bridge.py             MAGRGB strip
   |                                                        (HAP-BLE)
   |-- HTTP GET /json/info --> WLEDHttpHandler
   |                          (device discovery)
   |
   |-- UDP DRGB packets -----> WLEDUdpHandler
        (per-frame colors)     averages pixels
                               → set_color(r, g, b)
                                      |
                               hap_loop() (asyncio)
                               rgb_to_hsv conversion
                               put_characteristics(hue, sat, bri)
                                      |
                               ChaCha20-Poly1305          GATT writes
                               encrypted HAP session  ------------->
```

**Key protocol constraints:**
- Colors must be written as **HSV**, not RGB (HAP Lightbulb service uses Hue/Saturation/Brightness characteristics)
- `(0,0,0)` does NOT turn off the strip — you must write `False` to the `On` characteristic (IID 51)
- Max update rate is ~10 Hz due to HAP-BLE round-trip latency
- All GATT writes are encrypted (ChaCha20-Poly1305) after the Pair-Verify handshake

## HAP Characteristic IIDs

| Characteristic | IID | Type |
|---|---|---|
| On / Off | 51 | bool |
| Brightness | 52 | int 0–100 |
| Hue | 53 | int 0–360 |
| Saturation | 54 | int 0–100 |

## Windows Auto-Start

Use Task Scheduler to run `magnus_wled_bridge.py` at startup with **"Run with highest privileges"** and a 30-second startup delay. See README.md for full steps.

## Nanoleaf Animation Protocol (Per-Zone Control) — NOT WORKING

> **The animation path is broken in practice.** Writes to IID 60 (`COMMAND_INTERFACE`) are accepted at the HAP/GATT level without errors but the device does not apply them — strip stays white.
> The bridge now uses lightbulb characteristics (IID 51–54) for reliable single-colour control.
> See README.md § Known Issues for the full investigation.

The MAGRGB exposes animation control via the **COMMAND_INTERFACE** HAP characteristic (IID=60, UUID `A28E1902-CFA1-4D37-A10F-0071CEEEEEBD`) — part of the Lightbulb service. ~~Confirmed working.~~ **Does not work — device ignores writes.**

### Wire Format

Write to `put_characteristics([(AID, 60, payload)])`:

```
payload = [0x07, 0x01, len_hi, len_lo] + metaDataTlv + paletteTlv
           ───────────────────────────
           DISPLAY_SCENE command header (TlvType "0701")

metaDataTlv  (tag=0x01, 1-byte length):
  STRIPES: [tag=0x01, len=0x05, sceneId, 0x06, transitTime, direction, segment]
  FLOW:    [tag=0x01, len=0x06, sceneId, 0x05, transitTime, waitTime, direction, loopByte]

paletteTlv   (tag=0x02, 1-byte length, max 84 colors = 253-byte value):
  [tag=0x02, len=1+3*N, numColors, ...3 bytes/color]
  Color (24-bit big-endian): (repeat<<23) | (hue_0-360<<14) | (sat_0-100<<7) | bri_0-100
```

**Production config (bridge):** 60 zones, `segment=2`, `transit_time=0` (instant). Packet = 190 bytes.

### Effect Types

| Effect | Byte | Params | Notes |
|---|---|---|---|
| STRIPES | 0x06 | transitTime, direction, segment | Secretlab-specific. `segment`=% of strip per color zone |
| FLOW | 0x05 | transitTime, waitTime, direction, loop | Secretlab-specific. Flowing gradient |
| STREAM_CONTROL | 0x04 | (none) | Streaming mode — not yet tested |
| FADE | 0x01 | transitTime, waitTime, loop | Standard |

### Command Header Types

| Constant | Bytes | Meaning |
|---|---|---|
| DISPLAY_SCENE | `07 01` | Show scene immediately (**confirmed working**) |
| ADD_SCENE | `07 02` | Upload/save scene to device |
| DISPLAY_TEMP | `07 08` | Show temporary scene (no save) |

### Key Findings

- Animation IS sent via HAP (encrypted), NOT via the LTPDU service (`n.java` line 506 explicitly filters it out)
- `A18E6903` (ANIMATION_WRITE) does not appear in the GATT/HAP tables — `COMMAND_INTERFACE` (IID=60) is the actual write target
- STRIPES `segment` = % of strip per color. 60 colors × segment=2 → 60 equal zones filling 100%
- Tested zone counts: 7, 20, 60, 84 (1-byte palette max), 123 (2-byte palette, experimental) — all work
- Response comes back on `ANIMATION_WRITE_RESPONSE` (`A18E690C`) as a notification
- LTPDU crypto fully reverse-engineered (Curve25519+AES-CTR, salts `AES-NL-OPENAPI-KEY`/`AES-NL-OPENAPI-IV`) but not used for animation

### Source

Reverse-engineered from Nanoleaf Android app (`me.nanoleaf.nanoleaf`) via JADX:
- `CommandCentreRepository.java` line 6203/10806 — confirms `Ee.a(TlvType.DISPLAY_SCENE, scene.toByteArray()).formattedByteArray()` written to COMMAND_INTERFACE
- `SimpleScene.java` — TLV2 wire format
- `EffectType.java` — effect byte values (STRIPES=6, FLOW=5, STREAM_CONTROL=4)
- `TlvType.java` — command header bytes (DISPLAY_SCENE="0701", etc.)

## Notes

- `pairing.json` contains long-term private HAP keys — never commit it (already in `.gitignore`)
- `config_local.py` contains your personal device MAC — also gitignored
- The bridge binds to `127.0.0.2` (not `127.0.0.1`) to avoid conflicts with other loopback services
- The device advertises on two BLE addresses: one for HAP (Apple manufacturer ID 76) and one for Nanoleaf LTPDU — only the Apple address is used here
