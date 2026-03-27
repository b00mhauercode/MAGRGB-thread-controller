# Secretlab MAGRGB BLE Controller

> Full Python controller, SignalRGB integration, and technical protocol documentation for the **Secretlab Magnus XL RGB Strip** (co-developed with Nanoleaf, sold as MAGRGB).

---

## Table of Contents

1. [Device Identification](#device-identification)
2. [The Reverse Engineering Journey](#the-reverse-engineering-journey)
3. [Protocol Specification](#protocol-specification)
4. [Initial Setup](#initial-setup)
5. [Script Reference](#script-reference)
6. [SignalRGB Integration](#signalrgb-integration)
7. [Architecture](#architecture)
8. [Files in This Repo](#files-in-this-repo)
9. [Future Work](#future-work)
10. [Legal](#legal)

---

## Device Identification

| Field | Value |
|---|---|
| Product | Secretlab Magnus XL RGB Desk Strip |
| OEM | Nanoleaf (sold as MAGRGB) |
| BLE Advertised Name | `Secretlab MAGRGB XXBJ` |
| Protocol | HAP-BLE (Apple HomeKit Accessory Protocol over Bluetooth LE) |
| Power | USB |

### BLE Advertisement

After factory reset the device advertises on **two simultaneous addresses** — one per protocol:

| Address | Manufacturer ID | Protocol | Purpose |
|---|---|---|---|
| `XX:XX:XX:XX:XX:XX` | `76` (Apple) | HAP-BLE | HomeKit pairing + control |
| `YY:YY:YY:YY:YY:YY` | `2059` (Nanoleaf) | LTPDU | Nanoleaf app + Thread control |

> **Note:** Both addresses change after each factory reset. Use `scan.py` to find the current ones.

### HAP-BLE GATT Services

| Service UUID | Purpose |
|---|---|
| `00001800-0000-1000-8000-00805f9b34fb` | Generic Access (device name) |
| `0000003e-0000-1000-8000-0026bb765291` | HAP Accessory Information |
| `00000055-0000-1000-8000-0026bb765291` | HAP Pairing (Pair-Setup `0x4C`, Pair-Verify `0x4E`) |
| `00000043-0000-1000-8000-0026bb765291` | **HAP Lightbulb** — color control lives here |
| `00000701-0000-1000-8000-0026bb765291` | Nanoleaf scene/effect service |
| `6d2ae1c4-9aea-11ea-bb37-0242ac130002` | Nanoleaf LTPDU transport (encrypted) |

### HAP Lightbulb Characteristic IIDs

| Characteristic | HAP UUID | IID |
|---|---|---|
| On / Off | `00000025-...-0026bb765291` | 51 |
| Brightness (0–100) | `00000008-...-0026bb765291` | 52 |
| Hue (0–360°) | `00000013-...-0026bb765291` | 53 |
| Saturation (0–100%) | `0000002f-...-0026bb765291` | 54 |

---

## The Reverse Engineering Journey

### Phase 1 — Identifying the Protocol

The strip is controlled via the **Nanoleaf app** on Android/iOS, which meant the BLE traffic would tell us the protocol. The first step was an Android HCI snoop log capture.

**What the snoop log showed:**

The log captured **encrypted variable-length packets** to handles `0x008e` and `0x0090` — the actual Nanoleaf app traffic to the MAGRGB, encrypted using X25519 + AES-CTR (Nanoleaf's LTPDU protocol) or HAP-BLE ChaCha20-Poly1305. Since the keys are ephemeral per-session, these packets cannot be decrypted from the capture alone.

### Phase 2 — Understanding the Dual-Protocol Architecture

A GATT service enumeration (see `discover_services.py`) revealed the device exposes **both** HAP-BLE and Nanoleaf LTPDU service trees simultaneously:

- HAP services (`0026bb765291` UUID namespace) → Apple HomeKit protocol
- LTPDU service (`6d2ae1c4-9aea-11ea-bb37-0242ac130002`) → Nanoleaf proprietary protocol over Thread/CoAP

The device also advertises with **two manufacturer IDs** — Apple's (76) for HomeKit discovery and Nanoleaf's (2059) for the Nanoleaf app — on separate rotating BLE addresses.

This explains why aiohomekit's BLE scanner (which filters for Apple manufacturer ID 76) couldn't find the device during normal operation: it was paired and broadcasting an encrypted notification advertisement rather than a pairable one.

### Phase 3 — HAP-BLE Pairing

After factory resetting the device, the HAP address broadcasts a standard unencrypted HomeKit advertisement (manufacturer data starting with `0x06`). aiohomekit's normal discovery still couldn't find it because the advertisement used **Nanoleaf's advertisement address**, not the Apple one.

**Solution:** Bypass aiohomekit's scanner-based discovery entirely. Use `BleakScanner.find_device_by_address()` to get the BLEDevice directly, then invoke aiohomekit's low-level `drive_pairing_state_machine()` directly with the HAP Pair-Setup characteristic (`0x4C`).

The SRP pairing uses the **8-digit HomeKit setup code** printed on the device in `XXX-XX-XXX` format. The format matters — aiohomekit's `check_pin_format` rejects any other format.

**Pairing flow:**
```
1. BleakScanner.find_device_by_address(HAP_MAC)
2. AIOHomeKitBleakClient.connect()
3. drive_pairing_state_machine(PAIR_SETUP, perform_pair_setup_part1())
   → device returns SRP salt + public key
4. drive_pairing_state_machine(PAIR_SETUP, perform_pair_setup_part2(pin, uuid, salt, pubkey))
   → device returns long-term key pair (AccessoryLTPK, iOSDeviceLTSK, etc.)
5. Save pairing_data to pairing.json
```

### Phase 4 — Characteristic Control

With a valid pairing, HAP-BLE control works through aiohomekit's `BlePairing.put_characteristics()`. One compatibility issue: aiohomekit's `BlePairing` class expects to be driven by the full controller/scanner infrastructure, and crashes if `_accessories_state` is `None` when advertisement callbacks fire.

**Fix:** Pre-initialize `_accessories_state` with an empty `AccessoriesState(Accessories(), 0, None, 0)` immediately after loading the pairing.

Color is communicated in **HSV space** (not RGB), as HAP's Lightbulb service uses Hue + Saturation + Brightness as separate characteristics. RGB values from SignalRGB's WLED DRGB stream are converted with Python's `colorsys.rgb_to_hsv()`.

---

## Protocol Specification

### HAP-BLE Session

HAP-BLE uses a standard Pair-Verify handshake (X25519 + Ed25519) after pairing to establish a ChaCha20-Poly1305 encrypted session. aiohomekit handles this transparently.

### Characteristic Write Format

Control packets go through aiohomekit's `put_characteristics([(aid, iid, value)])`:

| Characteristic | IID | Type |
|---|---|---|
| On / Off | `51` | boolean |
| Brightness | `52` | int 0–100 |
| Hue | `53` | int 0–360 |
| Saturation | `54` | int 0–100 |
| COMMAND_INTERFACE | `60` | bytes — Nanoleaf animation writes |

### Off vs On

HAP has a discrete On/Off characteristic (IID 51). Sending RGB `(0,0,0)` does NOT turn off the light — you must write `False` to IID_ON. The bridge handles this automatically.

---

## Per-Zone Control (Animation Protocol)

The bridge uses the **Nanoleaf Animation Protocol** via HAP COMMAND_INTERFACE (IID 60, UUID `A28E1902`) to achieve per-zone color control across 60 independent zones. This was reverse-engineered from the Nanoleaf Android app (`me.nanoleaf.nanoleaf`) using JADX.

### Discovery Path

The device exposes a Nanoleaf Animation Service (`A18E6901-...`) with several characteristics, but **none of these are accessible via HAP** — they are filtered out at the firmware level. The actual animation path was found in `CommandCentreRepository.java`:

```java
// Line 6203 / 10806 — writes animation to COMMAND_INTERFACE, not ANIMATION_WRITE
new Ee.a(TlvType.DISPLAY_SCENE, scene.toByteArray(accessoryType)).formattedByteArray()
```

The LTPDU service (`6D2AE1C4-...`) uses a separate Curve25519 + AES-CTR encrypted channel but **animation commands are explicitly filtered out of LTPDU** (`n.java` line 506). All animation traffic goes through the HAP session.

### Wire Format

Every animation write to IID=60 uses this outer frame:

```
[cmd_hi, cmd_lo, len_hi, len_lo, ...TLV2 bytes...]
```

Where `cmd_hi cmd_lo` is the command type (DISPLAY_SCENE = `07 01`), followed by a 2-byte big-endian length, followed by the TLV2 animation payload.

**TLV2 payload** = `metaDataTlv + paletteTlv`

```
MetaData TLV (tag=0x01, 1-byte length):
  STRIPES (0x06):  [sceneId, 0x06, transitTime, direction, segment]
  FLOW    (0x05):  [sceneId, 0x05, transitTime, waitTime, direction, loopByte]
  FADE    (0x01):  [sceneId, 0x01, transitTime, waitTime, loopByte]

Palette TLV (tag=0x02, 1-byte length, max 84 colors):
  [numColors, c0_b2, c0_b1, c0_b0, ...]
  Each color is 3-byte big-endian:
    bit23     = repeat flag
    bits22-14 = hue   (0–360)
    bits13-7  = sat   (0–100)
    bits6-0   = bri   (0–100)
  i = (repeat<<23) | (hue<<14) | (sat<<7) | bri
```

### STRIPES Effect

STRIPES divides the strip into equal segments, one color per segment. The `segment` parameter is the width of each segment as a percentage of the total strip length:

| `segment` value | Zones | Result |
|---|---|---|
| 33 | 3 | three equal thirds |
| 14 | ~7 | rainbow |
| 5 | 20 | coarse zones |
| **2** | **60** | **production setting** |
| 1 | 84 | finest (max without 2-byte palette) |

STRIPES and FLOW are only supported on `SECRETLABS_LIGHT_STRIPS` device type — confirmed working on the MAGRGB.

### Production Configuration

The bridge uses **60 zones** (`segment=2`):
- 190-byte packet per frame — well within BLE MTU after HAP fragmentation
- Uses standard 1-byte TLV length (60 colors × 3 bytes + 1 = 181 bytes < 255)
- 123 SignalRGB pixels bucketed into 60 equal zones (~2 pixels per zone average)
- Visually indistinguishable from 84 or 123 zones at desk distance

```python
# Packet structure (190 bytes total):
# [07 01]          — DISPLAY_SCENE command
# [00 BC]          — TLV2 length = 188
# [01 05 01 06 00 00 02]   — MetaData TLV: STRIPES, transit=0, dir=0, seg=2
# [02 BD 3C ...]           — Palette TLV: 60 colors × 3 bytes
```

### Sources (Nanoleaf APK, JADX decompile)

| File | Finding |
|---|---|
| `CommandCentreRepository.java` | Animation writes to COMMAND_INTERFACE (IID=60), not ANIMATION_WRITE (A18E6903) |
| `EndpointLookup.java` | `Endpoints.CommandInterface` is the animation endpoint |
| `n.java` | CommandInterface commands filtered OUT of LTPDU — confirms HAP-only path |
| `SimpleScene.java` | TLV2 wire format (metaDataTlv + paletteTlv) |
| `EffectType.java` | Effect byte values (STRIPES=6, FLOW=5, FADE=1, RANDOM=2, HIGHLIGHT=3) |
| `TlvType2.java` / `Tlv2.java` | Tag byte constants and 1-byte length encoding |
| `ze/C8489a.java` | LTPDU Curve25519 + AES-CTR crypto (not used for animation) |

---

## Initial Setup

### Requirements

- Python 3.9+
- Windows 10/11 with Bluetooth LE adapter
- The device **factory reset** (hold reset button ~10s until light flashes)

> **Tested with:** Python 3.11, bleak 0.21, aiohomekit 3.2, SignalRGB 2.x, Windows 11 22H2+

```bash
pip install -r requirements.txt
```

### Step 1 — Find the Device MACs

```bash
python scan.py
```

Look for `Secretlab MAGRGB XXBJ`. Note both MAC addresses — you need the one with the **Apple manufacturer ID** for HAP pairing.

```bash
python scan_adv.py
```

This shows raw advertisement data. The HAP address has `Manufacturer: {76: '06...'}`.

### Step 2 — Update DEVICE_MAC in all scripts

Edit the `DEVICE_MAC` constant in **all four scripts** with the Apple manufacturer ID address found in Step 1:

- `pair.py`
- `magnus_wled_bridge.py`
- `test.py`
- `discover_services.py`

Each file has a `# EDIT THIS` comment above the constant.

### Step 3 — Pair (one-time)

```bash
python pair.py XXX-XX-XXX
```

Pass the 8-digit HomeKit setup code as a command-line argument. Format must be `XXX-XX-XXX`.

This creates `pairing.json` with your long-term keypair. **Keep this file and never commit it** — it contains your private HAP credentials. It is already listed in `.gitignore`.

### Step 4 — Test

```bash
python test.py
```

The strip should cycle: RED → GREEN → BLUE → WHITE 50% → OFF.

---

## Script Reference

| Script | Purpose | Usage |
|---|---|---|
| `scan.py` | List all nearby BLE devices | `python scan.py` |
| `scan_adv.py` | Show raw advertisement data for MAGRGB addresses | `python scan_adv.py` |
| `discover_services.py` | Enumerate GATT services and characteristics | `python discover_services.py` |
| `pair.py` | One-time HAP-BLE pairing, saves `pairing.json` | `python pair.py XXX-XX-XXX` |
| `test.py` | Color cycle test — RED/GREEN/BLUE/WHITE/OFF | `python test.py` |
| `magnus_wled_bridge.py` | SignalRGB WLED bridge (run as Administrator) | `python magnus_wled_bridge.py` |

---

## SignalRGB Integration

The strip is exposed to SignalRGB as a **WLED device** — no custom plugin needed.

### Setup

**Step 1 — Start the bridge (run as Administrator for port 80)**

```bash
python magnus_wled_bridge.py
```

Output:
```
WLED UDP on 127.0.0.2:21325
WLED HTTP on :80
Waiting for XX:XX:XX:XX:XX:XX...
Found: Secretlab MAGRGB XXBJ
HAP-BLE loop running.
```

**Step 2 — Add in SignalRGB**

1. Open SignalRGB → **Home → Lighting Services → WLED**
2. In "Discover WLED device by IP" enter `127.0.0.2` and press Enter
3. SignalRGB calls `/json/info` on port 80, gets back `"brand": "WLED"` and adds the device
4. Click **Link** — **"Magnus RGB Strip"** is now on your canvas

> **Note:** If you also run the Manka boom arm bridge, it runs on `127.0.0.1:80`. The Magnus bridge runs on `127.0.0.2:80` — different loopback IPs, same port. SignalRGB discovers each by IP with no port suffix needed.

**Step 3 — Assign an effect**

Drag the Magnus RGB Strip block on your canvas and assign any effect.

### Windows Auto-Start

1. Open **Task Scheduler** → **Create Task**
2. **General:** Name `Magnus BLE Bridge`, check **Run with highest privileges**
3. **Triggers:** At startup, delay 30 seconds
4. **Actions:** Start `python`, arguments `C:\path\to\MAGRGB-controller\magnus_wled_bridge.py`, start in `C:\path\to\MAGRGB-controller`
5. Save

---

## Architecture

```
╔═══════════════════════════════════════════════════════════════════╗
║                         YOUR PC                                   ║
║                      (127.0.0.2)                                  ║
║                                                                   ║
║  ┌─────────────────┐      ┌────────────────────────────────────┐  ║
║  │   SignalRGB     │      │     magnus_wled_bridge.py          │  ║
║  │                 │      │                                    │  ║
║  │  Canvas effect  │─────▶│  HTTP :80    (WLED discovery)      │  ║
║  │  assigns color  │ UDP  │  UDP  :21325 (DRGB color stream)   │  ║
║  │  to MAGRGB      │─────▶│                                    │  ║
║  └─────────────────┘      │  bucket 123px → 60 zones           │  ║
║                            │  RGB→HSV, STRIPES packet → IID=60  │  ║
║                            │  HAP-BLE asyncio loop, max 10 Hz  │  ║
║                            └───────────────┬────────────────────┘  ║
║                                            │ HAP-BLE               ║
║                                            │ (ChaCha20-Poly1305    ║
║                                            │  encrypted GATT)      ║
╚════════════════════════════════════════════╪══════════════════════╝
                                             │
                                    ┌────────▼────────┐
                                    │  Secretlab      │
                                    │  MAGRGB         │
                                    │  HAP-BLE GATT   │
                                    └─────────────────┘
```

**Key differences from raw GATT approaches:**
- All BLE writes are encrypted (ChaCha20-Poly1305) — HAP session encryption
- Color is HSV not RGB — converted before each write
- On/Off is a separate characteristic — must be set explicitly, not inferred from black color
- Max ~10 Hz update rate (HAP-BLE round-trip is slower than raw GATT)

---

## Files in This Repo

| File | Purpose |
|---|---|
| `pair.py` | **One-time pairing** — SRP pairing via HAP-BLE, saves `pairing.json` |
| `magnus_wled_bridge.py` | **Main integration** — WLED emulator + HAP-BLE bridge for SignalRGB |
| `test.py` | Color cycle test — verifies control works after pairing |
| `scan.py` | BLE scanner — lists all nearby devices with names and MACs |
| `scan_adv.py` | Advertisement scanner — shows raw manufacturer data for MAGRGB addresses |
| `discover_services.py` | GATT service enumerator — lists all services and characteristics |
| `compat.py` | Bleak 2.x compatibility shim used by bridge and test scripts |
| `requirements.txt` | Python dependencies with minimum version bounds |
| `pairing.json` | **Your long-term keypair** — generated by `pair.py`, gitignored (see `pairing.json.example`) |
| `pairing.json.example` | Schema reference for `pairing.json` with redacted placeholder values |

---

## Future Work

- [x] Per-zone color control — 60-zone STRIPES via COMMAND_INTERFACE (IID=60), confirmed working
- [ ] Auto-detect HAP MAC address on startup (handles address rotation after reset)
- [ ] Apple HomeKit re-integration alongside bridge (HAP supports up to 16 controllers)
- [ ] LTPDU integration — crypto is fully reverse-engineered (Curve25519 + AES-CTR); could enable firmware or non-animation commands. Animation itself goes via HAP, not LTPDU.

---

## Legal

This project was developed for personal interoperability use with hardware the author owns. Reverse engineering for interoperability purposes is permitted under DMCA §1201(f) (US) and equivalent provisions in other jurisdictions.

Not affiliated with, endorsed by, or connected to Secretlab, Nanoleaf, or Apple. All trademarks are property of their respective owners.

Use at your own risk.
