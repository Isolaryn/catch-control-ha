# CATCH Control for Home Assistant

Unofficial custom integration for CATCH Control 2CH. It operates locally over
Bluetooth or over a device-initiated TLS WebSocket; the CATCH does not need
internet access. Requires **Home Assistant 2026.9.1 or newer**. Automated tests
target 2026.9.1.

## Install

1. Download `catch-control-home-assistant-0.3.0.zip` from
   [GitHub releases](https://github.com/Isolaryn/catch-control-ha/releases).
2. Extract it into the Home Assistant configuration directory. The result must
   include `custom_components/catch_control/manifest.json`. On HA OS this is
   normally below `/config`.
3. Restart HA and add **CATCH Control** from Settings → Devices & services.
4. Close Configurator on the phone before setup or any later endpoint change.

Existing 0.2.x entries migrate to Bluetooth mode. This is a manually installed
custom integration, not a HACS or HA Core listing.

## Connection modes

### Bluetooth

HA polls through its shared Bluetooth manager, using a local adapter or a
**connectable** Bluetooth proxy. It connects and disconnects for each transaction.
Select a discovered device or enter its address. A password is optional for
monitoring and required for schedule changes.

A macOS CoreBluetooth UUID is not portable to a Linux HA host. Use the address
that HA discovers. Temporarily disable the integration when Configurator needs
an uninterrupted Bluetooth session.

### Wi-Fi WebSocket server

Wi-Fi mode keeps normal control traffic on the IoT LAN:

1. Choose **Wi-Fi WebSocket server** during setup.
2. Select the device over Bluetooth and supply its local settings password.
3. Enter a DNS name or IPv4 address that resolves to the HA host from the device
   VLAN, an unused TCP port from 1024–32767, and the local bind address.
4. Allow the device to initiate TCP connections to that HA address and port.

HA starts its TLS listener first, then uses authenticated Bluetooth to set and
verify both device server slots. After that first setup, polling and schedule
changes use `wss://<host>:<port>/srwe`; normal restarts do not require Bluetooth.
Changing the advertised host or port in integration options performs another
authenticated Bluetooth update during reload.

Each Wi-Fi device currently needs a distinct listen port. The advertised host
must contain only DNS/IPv4 hostname characters and fit the firmware's 29-byte
field. Do not include `https://`, `wss://`, a path, or a port in that field.

On first Wi-Fi startup, HA generates a 2048-bit RSA self-signed certificate and
retains it for ten years under `.storage/catch_control`. The key file is created
with mode 0600. Firmware 12718 was observed to require RSA TLS compatibility but
to use `VERIFY_NONE` for this connection, so no public CA or private root is
needed and changing the certificate does not require changing the device. The
firmware does not authenticate its server certificate; restrict the listener to
the intended IoT network and protect the HA host.

The previous primary and secondary server names and ports are retained in the
config entry when Wi-Fi mode first configures the device. Removing the entry does
not currently restore them automatically. Change the endpoint deliberately
before removing HA if the device should reconnect somewhere else.

## Entities and operation

The integration polls every 30 seconds; options allow 15–3600 seconds.

| Entities | Meaning |
| --- | --- |
| 16 sensors | Voltage, frequency, operating mode, reported server status, Wi-Fi signal, reported runtime; current, power, apparent power, reactive power and power factor per CT channel |
| 4 switches | Enable/disable each schedule |
| 4 selects | Each schedule's operating mode |
| 8 time controls | Each schedule's start and stop |

Schedule controls are configuration entities. Firmware 12718 is required. In
Bluetooth mode they also require the stored password. Wi-Fi mode writes exactly
one validated 7-byte schedule record, checks that all schedules are still fresh,
and verifies all schedules afterward.

**The switches enable schedules; they do not directly switch a load immediately.**
CT channels measure power and are not independently controlled relay outputs.
Physical load switching has not been validated by this project.

If telemetry succeeds but configuration fails, sensors remain available while
schedule controls become unavailable. Configuration is retried on the next poll.
A failed write is never automatically repeated because it may already have taken
effect.

## Combined schedule action

`catch_control.set_schedule` changes all fields in one verified save. Target the
enabled switch for the desired slot. This example keeps schedule 4 disabled:

```yaml
action: catch_control.set_schedule
target:
  entity_id: switch.catch_control_4242_schedule_4_enabled
data:
  active: false
  mode: turn_off
  start: "14:00"
  stop: "14:05"
```

Modes: `default`, `export`, `turn_on`, `turn_off`, `top_up`, `voltage`,
`frequency`. An active schedule cannot use `default` or cross midnight. Times
are whole minutes in device local time. Newly overlapping or touching active
windows are rejected. Use the combined action or disable a slot first if editing
one field would create an invalid intermediate window.

## Credentials, troubleshooting and development

The password is stored in config-entry options in HA's local `.storage`. Protect
storage and backups. Options do not prefill it: a blank replacement keeps the
current password. Diagnostics omit credentials, device address, serial number,
raw packets, server host, and certificate details.

For Bluetooth timeouts, enable debug logging, reproduce the operation, and
download diagnostics. Debug logs contain operation numbers and timing but no
packet contents. A `CancelledError` during reload or restart means HA cancelled
an in-flight operation; check earlier errors for the original failure.

The library is bundled under `_vendor`. Edit `library/src/catch_control`, then
build and test from the repository root:

```sh
uv run python tools/build_ha.py
docker build -f home-assistant/Dockerfile.test -t catch-control-ha-test home-assistant
docker run --rm --mount "type=bind,source=$PWD/home-assistant,target=/workspace,readonly" catch-control-ha-test
```

Tests use real HA classes and synthetic device responses. BLE and WSS telemetry,
configuration, and a reversible inactive-schedule edit were also validated on
firmware 12718. The HA-host Wi-Fi deployment still needs a hardware check on the
target installation.
