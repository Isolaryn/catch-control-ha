# Protocol and validation notes

Updated 2026-09-07. Public notes exclude device identifiers, credentials, raw
captures and third-party APK/firmware contents. Detailed private investigation
artifacts remain outside version control.

## Evidence and scope

The official Android Configurator's ordinary BLE settings and telemetry flows
were inspected using an owner-authorized installed APK (2.10.15). JADX exposed
the native BLE wrapper; application logic supplied frame and field layouts.
Firmware 12718 was inspected with Ghidra to establish network behavior. No
firmware was flashed and no authentication bypass was implemented.

The implementation is split into a reusable library, CLI and HA integration.
Binary layouts in `library/src/catch_control/schemas.py` use
[Construct](https://construct.readthedocs.io/) with named fields, explicit
endianness, widths and padding. Tests independently pack synthetic wire fixtures.

## Local BLE protocol

| Item | Value |
| --- | --- |
| Model | 10004, CATCH Control 2CH |
| Verified write firmware | 12718 |
| Advertisement | Four manufacturer bytes interpreted as a big-endian model number; Bleak separates the first two as its little-endian company ID |
| Service | `49535343-fe7d-4ae5-8fa9-9fafd205e455` |
| Write/notify characteristic | `49535343-1e4d-4bd9-ba61-23c647249616` |
| Frame | 1-byte opcode, 252-byte payload, 2-byte CRC |
| Numeric scalars | Packed little-endian |
| CRC | CRC-16/Modbus, initial `0xffff`, polynomial `0xa001`, high byte first on wire |
| Operations | Identity 0, configuration read 1, save 2, telemetry 3 |

Messages use the negotiated BLE write limit and 40 ms fragment spacing.
Notifications are reassembled and CRC checked. Replies lack transaction IDs;
failed reads disconnect so late data cannot satisfy a later request.

Configuration reads omit the credential. Saves require a supplied, matching
password and preserve unrelated settings plus unknown extension bytes. A fresh
read rejects stale plans. Saving need not produce an acknowledgement: the client
waits two seconds and compares readback, allowing the device clock to advance.
Verification failures are reported without automatically repeating the save.

Schedules use minutes from midnight. The official
[Configurator guide](https://www.catchpower.com.au/_files/ugd/cfbb6e_1a7740aefa8d4a269a88e425325fb5ff.pdf)
advises avoiding overlaps. Endpoint inclusivity, overnight scheduling and
overlapping-slot priority are unverified. New overlapping/touching active windows
and overnight windows are conservatively rejected. Existing overlaps can be
read and disabled.

## Wi-Fi findings

Firmware uses an outbound secure WebSocket client. A direct LAN control API
has not been established. Self-hosted server compatibility and TLS trust
behavior have not been tested. No DNS redirection or TLS changes are needed
for the working BLE client.

The owner confirmed the device's VLAN has no internet access. BLE reads and
authenticated schedule saves still worked. The firewall was not independently
inspected. The device continued reporting Server Good; that value is not
independent evidence of a current cloud connection.

## Validation and limits

On model 10004 / firmware 12718, repeated sessions read identity, configuration
and telemetry. A 12-sample polling session left settings unchanged except the
advancing clock. An inactive schedule was changed, readback verified, restored
and verified again. Other settings matched the baseline and the operating mode
remained unchanged. This was repeated on the owner-confirmed internet-blocked
VLAN. No enabled schedule, electrical parameter, firmware or network rule was
changed by those tests. Physical load switching was not tested.

Software tests cover CRC/framing, fragmentation, signed telemetry, omitted
credentials, wrong passwords, field preservation, stale plans, invalid windows,
no-op avoidance, unacknowledged/ignored saves and host-managed connections. CLI
tests cover argument validation, JSON, private files and preview behavior.
HA tests target 2026.9.1 with simulated Bluetooth responses.

Remaining work includes hardware validation through HA adapters/proxies,
physical load switching, other models/firmware, temporary one-shot overrides,
overnight schedules, energy-counter reset semantics and optional local Wi-Fi
control. These capabilities are not claimed as implemented or tested.

## HA setup timeout follow-up (0.2.1)

An HA installation completed telemetry but timed out waiting for configuration.
A debug reload cancelled another wait at the same operation. The owner reported
that restarting HA restored operation. This establishes recovery, not the exact
cause of the missing reply.

The patch keeps valid telemetry available when configuration fails, marks
schedules unavailable until the next successful configuration poll, separates
connection/read deadlines, bounds notification setup and disconnect cleanup,
and adds metadata-only transport debug logs. Regression tests exercise partial
setup/recovery, actual task cancellation, a missing reply and stalled cleanup.
