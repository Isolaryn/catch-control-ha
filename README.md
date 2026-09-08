# CATCH Control local access

Three projects for using a CATCH Control 2CH without giving the device internet
access. Unofficial; not affiliated with CATCH Power.

| Project | Purpose | Installation |
| --- | --- | --- |
| [Python library](library/) | Async BLE client plus bidirectional device-initiated WebSocket sessions, using Construct layouts | Install the library wheel or `pip install ./library` |
| [CLI](cli/) | BLE discovery/control and a TLS WebSocket telemetry listener | Install both wheels or `pip install ./library ./cli` |
| [Home Assistant integration](home-assistant/) | Selectable Bluetooth or Wi-Fi transport, 16 sensors and 16 schedule controls | Extract the integration ZIP into your HA configuration directory and restart |

Download installable artifacts from [GitHub releases](https://github.com/Isolaryn/catch-control-ha/releases).
Neither Python package is currently published to PyPI. The HA ZIP bundles the
same library source used by the CLI, so installation does not depend on an
unpublished Python package.

## Quick start

Python 3.11+ and a working Bluetooth adapter are required for the library/CLI.
From this checkout with [uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked
uv run catch-control scan
uv run catch-control identity
uv run catch-control read
uv run catch-control configuration
uv run catch-control watch --interval 5 --count 3
```

Close the phone's Configurator connection first. With multiple devices, pass
`--address` using an identifier from `scan`. macOS identifiers are CoreBluetooth
UUIDs; Home Assistant on Linux normally uses Bluetooth MAC addresses.

The Home Assistant integration requires HA 2026.9.1 or newer. Bluetooth mode
uses a local adapter or connectable proxy. Wi-Fi mode uses Bluetooth once to
configure the device's outbound local server and then polls over WSS. See the
[installation guide](home-assistant/README.md) for setup and automation examples.

## Supported behavior

Identity, telemetry and configuration reads work over BLE. Configuration output
omits the credential. Authenticated schedule changes are restricted to **model
10004, firmware 12718**, whose layout was verified. Four schedules expose their
enabled state, mode and start/stop times. These are scheduled operating modes;
the two CT measurement channels are not independent switch outputs.

Writes preserve unrelated fields and unknown bytes, reject newly overlapping
active windows, check for stale configuration and verify readback. Overnight
active windows are not supported. A failed verification is an uncertain outcome;
the client does not automatically repeat a save. There is no verified atomic
compare-and-swap device operation, so avoid simultaneous configuration editors.

No cloud requests are made by the client. Internet may be needed on the host
to install dependencies; the CATCH device can remain on its isolated network.
Firmware 12718 initiates `wss://<configured-host>:<port>/srwe`. The verified
request/response protocol provides telemetry, schedule reads, and single-record
schedule writes. Wi-Fi endpoint setup is authenticated and verified over BLE.

## Validation

Real-device BLE and WSS reads plus changing/restoring an **inactive** schedule
over each transport were verified on firmware 12718 with the device on an
internet-blocked VLAN. Physical load switching has not been tested. Home
Assistant behavior is tested against HA 2026.9.1 with simulated device
responses; the new HA Wi-Fi mode still needs validation on the target HA host.
See [protocol and validation notes](RESEARCH.md).

```sh
uv run python -m unittest discover -s library/tests -v
uv run python -m unittest discover -s cli/tests -v
docker build -f home-assistant/Dockerfile.test -t catch-control-ha-test home-assistant
docker run --rm --mount "type=bind,source=$PWD/home-assistant,target=/workspace,readonly" catch-control-ha-test
```

## Build

```sh
uv build --package catch-control
uv build --package catch-control-cli
uv run python tools/build_ha.py
```

Outputs go to `dist/`. Edit canonical library code in `library/src/catch_control`,
then regenerate the HA bundle with `tools/build_ha.py`. Its file hashes are
recorded in `_vendor/library.json`. Never edit the generated copy directly.

Credentials belong in an owner-only file under `.secrets/`, which is ignored.
Device captures, APKs, firmware, local environments and build outputs are also
excluded from version control. Published tests use synthetic identifiers/data.
