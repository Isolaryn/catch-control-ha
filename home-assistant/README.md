# CATCH Control Bluetooth for Home Assistant

Unofficial custom integration for CATCH Control 2CH. Uses local Bluetooth;
the device does not need internet access. Requires **Home Assistant 2026.9.1
or newer**. Automated tests target 2026.9.1.

## Install

1. Download `catch-control-home-assistant-0.2.1.zip` from
   [GitHub releases](https://github.com/Isolaryn/catch-control-ha/releases).
2. Extract into your Home Assistant **configuration directory**. The result
   must be `custom_components/catch_control/manifest.json` alongside the other
   integration files. On HA OS this directory is normally `/config`. Alternatively
   copy `home-assistant/custom_components/catch_control` from this checkout.
3. Restart HA. Ensure its Bluetooth integration has a working local adapter or
   **connectable** Bluetooth proxy within range of the CATCH.
4. Close/disconnect Configurator on your phone. Go to **Settings → Devices &
   services → Add integration → CATCH Control Bluetooth**, or use its discovered
   device card. Select the device or enter its Bluetooth address.
5. Enter the local device settings password to enable schedule changes, or leave
   it blank for monitoring. This is not a cloud account password.

HA may download dependencies at first setup; the device's IoT VLAN can remain
isolated. This is a manual custom integration, not a HACS or HA Core listing.
The shared [HA Bluetooth APIs](https://developers.home-assistant.io/docs/core/bluetooth/api/)
select the adapter/proxy. A macOS CoreBluetooth UUID is not portable to a Linux
HA host: use the device discovered by HA.

The integration polls every 30 seconds and disconnects between transactions.
Options allow 15–3600 seconds. Temporarily disable the integration when you need
an uninterrupted Configurator session.

If telemetry succeeds but configuration fails, sensors remain available while
schedule controls become unavailable. Configuration is read again on the next
poll; successful readback restores the controls. Old schedules are not shown
as fresh data. Connection establishment allows 30 seconds, individual reads
10 seconds, and disconnect cleanup at most 5 seconds.

### Troubleshooting a configuration timeout

Enable debug logging from the integration's menu, reproduce the problem, then
disable debug logging to download the log. Version 0.2.1 adds operation numbers,
elapsed time, sent chunk counts, received fragment/byte counts and valid frame
counts. It does not log packet contents or passwords. Opcode 1 is configuration;
0 is identity and 3 is telemetry. Diagnostics include the failed read stage and
whether fresh configuration is available.

A `CancelledError` during Reload or restart means HA cancelled the in-flight
operation. Check earlier errors for the original failure. One reported setup
configuration timeout cleared after restarting HA; its exact cause remains
unconfirmed. A restart is a recovery option if a normal reload does not help.

## Entities

| Entities | Meaning |
| --- | --- |
| 16 sensors | Voltage, frequency, operating mode, reported server status, Wi-Fi signal, reported runtime; current, power, apparent power, reactive power and power factor per CT channel |
| 4 switches | Enable/disable each schedule |
| 4 selects | Each schedule's operating mode |
| 8 time controls | Each schedule's start and stop |

Schedule controls are configuration entities. They remain unavailable until a
password is configured and firmware 12718 is detected. Monitoring works without
a password. Energy counters are omitted from HA statistics because their signed/
reset behavior is unverified. Reported server status is a device value, not proof
of current cloud connectivity.

**The switches enable schedules; they do not directly switch a load immediately.**
CT channels measure power and are not independently controlled relay outputs.
Physical load switching has not been validated by this project.

## Combined schedule action

`catch_control.set_schedule` changes all fields in one verified save. Target the
enabled switch for the desired slot; substitute your actual entity ID below.
This example keeps schedule 4 disabled:

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

Modes: `default`, `export`, `turn_on`, `turn_off`, `top_up`, `voltage`, `frequency`.
An active schedule cannot use `default` or cross midnight. Times are whole
minutes, 00:00–23:59 in device local time. Newly overlapping/touching active
windows are rejected. Use the combined action or disable the slot first if
editing a single time would create an invalid intermediate window.

The library reads again before saving, preserves other fields and verifies
readback afterward. It never retries a save automatically. On an uncertain
outcome, refresh/read configuration before trying again. Avoid simultaneous
configuration editors.

## Credentials, updates and development

The password is stored in config-entry options in HA's local `.storage`.
Protect storage and backups. Options do not prefill the password: leaving a
replacement blank keeps it; **Clear password** returns to monitoring only.
Diagnostics omit credentials, device address and serial number.

To update, back up HA configuration, replace the integration directory and
restart. To remove, delete its integration entry in Settings, remove the custom
component directory and restart.

The library is bundled under `_vendor` because it is not published to PyPI.
Edit the canonical `library/src/catch_control` source and run these from the
repository root:

```sh
uv run python tools/build_ha.py
docker build -f home-assistant/Dockerfile.test -t catch-control-ha-test home-assistant
docker run --rm --mount "type=bind,source=$PWD/home-assistant,target=/workspace,readonly" catch-control-ha-test
```

Tests use real HA classes and simulated device responses, without Bluetooth
hardware access. Library hardware validation was performed on macOS. Deployment
through an HA adapter/proxy still needs a hardware check on your installation.
