# catch-control — reusable Python library

Python 3.11+ asynchronous local client for CATCH Control 2CH (model 10004).
It uses Bleak for setup/direct access and accepts device-initiated WebSocket
connections. Construct defines all wire layouts.

## Install and read

From the repository root: `pip install ./library`, or install the library wheel
from a GitHub release. This package is not published to PyPI. BLE requires
a platform-supported adapter and Bluetooth permissions.

```python
import asyncio
from catch_control.ble import CatchClient, discover

async def main():
    found = await discover(timeout=10)
    if len(found) != 1:
        raise RuntimeError("Select one device from discovery results")
    device, advertisement = found[0]
    async with CatchClient(device) as client:
        print(client.identity)
        print(await client.telemetry())
        print(await client.configuration())

asyncio.run(main())
```

Close Configurator's connection first. `CatchClient` accepts a Bleak `BLEDevice`
or an address. On macOS, the address is a CoreBluetooth UUID, not a portable MAC.
Telemetry uses V, Hz, A, W, VA and var; power factor is a ratio. Signed raw energy
counters remain available, but rollover/reset semantics are unverified. Enum
fields expose names alongside raw values. Configuration reads omit the credential
and preserve raw clock components without assuming calendar/timezone conversions.

## Plan and apply

Inside an open client context:

```python
from pathlib import Path
from catch_control import ControlMode, Schedule

password = Path('.secrets/catch-control.password').read_text().rstrip('\r\n')
plan = await client.plan_schedule(
    4, Schedule(False, ControlMode.TURN_OFF, 840, 845), password=password,
)
print(plan.summary())  # Preview only: disabled slot, 14:00–14:05.
# Save only when you intend to change this slot:
# result = await client.apply_schedule(plan, password=password)
```

`authenticate(password=...)` validates a supplied credential without saving.
Writes/authentication require the verified firmware 12718 format. Use `chmod 600`
on the password file and exclude it from version control. The library does not
log or return credential bytes.

Slots are 1–4; times are whole minutes 0–1439 in device local time. Active
windows must start before they stop, cannot use `DEFAULT`, and cannot introduce
overlap with another active slot. Touching endpoints count as overlap. Existing
overlaps can be read or disabled. Other settings and opaque bytes are preserved.
Apply rechecks freshness and verifies readback; it never retries a save.

## Local Wi-Fi server sessions

Firmware 12718 connects outward to `wss://<configured-host>:<port>/srwe`. A TLS
server supplies an accepted binary connection to `CatchWebSocketSession`:

```python
from catch_control.wifi import CatchWebSocketSession

session = CatchWebSocketSession(accepted_connection)
telemetry = await session.telemetry()
configuration = await session.configuration()
plan = await session.plan_schedule(
    4, Schedule(False, ControlMode.TURN_OFF, 840, 845)
)
result = await session.apply_schedule(plan)
```

The connection object needs async `send(bytes)` and `recv()` methods. Requests
are serialized because replies have no transaction identifier. Configuration
decoding consumes but never returns the embedded password. A schedule write
uses the firmware's validated 7-byte record operation, compares all four
schedules before saving, and verifies all four afterward.

Use `CatchClient.plan_websocket_server(host, port, password=...)` and
`apply_websocket_server(...)` over Bluetooth to set both primary and secondary
device server slots. The plan preserves IP, SSID, Wi-Fi credentials, thresholds,
and opaque extension bytes. Ports 1–32767 and 29-byte DNS/IPv4 host names are
supported by the verified firmware layout.

Handle `AuthenticationError`, `ConfigurationConflict`, `WriteVerificationError`
from `catch_control.configuration`, and `ProtocolError` from `catch_control`.
A verification error means a write may have happened: read configuration before
deciding whether to try again.

## Host-managed connections

`CatchClient(device, timeout=30, connector=connector)` supports shared Bluetooth
managers such as Home Assistant. The async connector receives
`(device, disconnected_callback, timeout)` and returns an **already connected**
Bleak-compatible client. CatchClient owns and disconnects that connection on
context exit or transport failure. See the HA project's `connection.py`.

Each client serializes requests. Notifications are reassembled and CRC checked;
writes respect the negotiated BLE chunk limit. Read failures disconnect because
replies lack transaction IDs. Use a new context to reconnect. Avoid concurrent
configuration editors: a freshness read cannot exclude another controller
saving immediately afterward.

`connect_timeout` optionally sets a separate connection-establishment deadline;
it defaults to `timeout`. Notification setup and each read use `timeout`.
Disconnect cleanup is bounded to five seconds. Debug logs contain only operation
numbers, timing and fragment/frame counts, never packet contents or credentials.

## Development

From the repository root:

```sh
uv run python -m unittest discover -s library/tests -v
uv build --package catch-control
```

Tests independently pack synthetic wire fixtures to check layouts, CRC,
fragmentation, credential omission, field preservation and verified writes.
