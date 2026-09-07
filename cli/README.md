# catch-control-cli

Command-line interface for the separate `catch-control` library. Python 3.11+.
From the repository root, install with `pip install ./library ./cli`, or install
both release wheels together. With uv, use `uv sync --locked` and prefix the
commands below with `uv run`. Neither package is published to PyPI.

```sh
catch-control scan
catch-control identity
catch-control configuration
catch-control read
catch-control watch --interval 5 --count 12
```

Close Configurator's connection before using another host. Commands select the
only matching device. With several devices, add `--address` using an identifier
from `scan`. macOS returns a CoreBluetooth UUID; Linux normally returns a MAC.
The Wi-Fi IP is not a Bluetooth identifier.

`read` emits JSON; `watch` emits one object per line. All read commands leave
settings unchanged. Configuration output omits the credential. Watch stops on
connection failure rather than silently reconnecting.

## Schedule changes

Store your local device password in `.secrets/catch-control.password`, use
directory mode 0700 and file mode 0600, and keep the directory ignored by Git.
The CLI rejects group/world-readable password files. Do not pass the password
as a command-line argument.

```sh
catch-control schedule --slot 4 --active no --mode turn_off \
  --start 14:00 --stop 14:05 --password-file .secrets/catch-control.password
```

This previews an authenticated edit of a disabled slot. Add **`--apply`** to
save and verify it. Supply all schedule fields explicitly. Writes support only
model 10004 / firmware 12718.

Modes: `default`, `export`, `turn_on`, `turn_off`, `top_up`, `voltage`, `frequency`.
`default` is allowed only when inactive. Active windows must start before they
stop within 00:00–23:59 in device local time. New overlaps, including touching
endpoints, are rejected. Disable a slot before editing an invalid intermediate
window, or submit all fields in one schedule command. These edit schedules;
they are not independent CT-channel relay switches.

Settings outside the chosen slot are preserved. The CLI checks freshness and
verifies readback. If verification fails, the save may still have happened.
Read configuration before retrying. Errors exit nonzero; previews and successful
reads/writes exit zero.

## Development

From the repository root:

```sh
uv run python -m unittest discover -s cli/tests -v
uv build --package catch-control-cli
```
