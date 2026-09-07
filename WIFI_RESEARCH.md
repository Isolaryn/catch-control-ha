# Wi-Fi control and diagnostics investigation

Updated 2026-09-07. This is an interoperability investigation, not a claim that
local Wi-Fi control is implemented. App and firmware artifacts stay private.

## What is established

| Route | Evidence | Current conclusion |
| --- | --- | --- |
| Direct HTTP/HTTPS on CATCH | Device answers ICMP; TCP connections to 80 and 443 were refused in this session | No web API at those ports at the time checked; other ports/protocols are not ruled out |
| Direct Modbus/TCP on CATCH | TCP 502 refused; vendor documents configure port 502 on the inverter | Published Modbus/TCP support does not establish an inbound CATCH control server |
| Outbound WebSocket | Firmware creates a secure WebSocket client and dispatches binary messages | Potential self-hosted route, requiring server configuration, normal TLS trust/server identity and application-protocol compatibility |
| Wi-Fi diagnostics over BLE | App declares read operations and firmware contains the extended signal-reply handler | Useful diagnostic path independent of a local Wi-Fi control API |

The vendor's [SMA installation guide](https://docsengine.catchpower.com.au/builder/doc/1KGdTkeST4og1yPz4GVC0LzfeesoqiQSf)
enables Modbus TCP on the **inverter**, with CATCH connecting to it. The
[Alpha ESS guide](https://docsengine.catchpower.com.au/builder/doc/1OW5VSNe5_WCncynE3VeM7HEOH2U01K38)
also describes the inverter's Ethernet interface. Neither supplies a CATCH
Modbus server register map. The vendor's [Configurator guide](https://www.catchpower.com.au/_files/ugd/cfbb6e_1a7740aefa8d4a269a88e425325fb5ff.pdf)
documents Bluetooth for local access.

## Firmware transport findings

Firmware 12718's WebSocket event handler accepts binary application messages
and routes them through a dispatcher distinct from the BLE dispatcher. The
command meanings and payload sizes differ. A server cannot simply send the
existing 255-byte BLE packets over WebSocket and assume equivalence.

The client constructs an outbound `wss://<host>:<port>/srwe` connection. Primary
and secondary hosts and ports come from mutable Wi-Fi settings. Configurator's
Wi-Fi setup code preserves those fields when changing SSID/credentials.
The hostnames are therefore configuration, not proof of a fixed cloud endpoint.

Changing the server settings, certificate compatibility, application handshake,
session authentication and safe control through a self-hosted endpoint remain
unverified. DNS redirection alone would not establish TLS or protocol compatibility.
No redirect, certificate change, listener deployment or device setting change was
made in this investigation. Firmware was not flashed.

## Read-only diagnostic paths

**Wi-Fi settings (BLE opcode 7):** the app defines IP mode, address, gateway,
DNS, subnet, two server host/port pairs and network credentials. Any future
public decoder must skip both Wi-Fi password fragments entirely and redact
network identifiers in exported diagnostics. Reading settings must not be
implemented by running the app's Wi-Fi scan wizard: its preparation path can
clear saved Wi-Fi configuration before scanning.

**Extended Wi-Fi health (BLE opcode 30):** the app defines a 43-byte packed
payload within the ordinary BLE frame. Firmware 12718 has a matching handler
that copies these fields into its response. This is stronger evidence than
merely finding a diagnostic string, but the device's live capability flags
and collected values still need to be read to establish runtime usefulness.

The payload includes:

- Capability flags for transmit rate, retries, SNR, gateway RTT, nearby SSIDs
  and application disconnect tracking.
- RSSI, access point BSSID and a BSSID-change flag.
- Maximum/transmit rate and SNR values in tenths.
- Gateway RTT median/jitter, nearby SSID count, Wi-Fi disconnect count,
  WebSocket disconnect count and WebSocket send-failure count.
- Collection window, raw health score, dominant metric and collection timestamp.

The app treats an all-zero response as unsupported/unavailable and falls back
to ordinary telemetry for the 2CH model. Capability flags must accompany metrics;
zero or sentinel values must not be promoted to confirmed healthy measurements.
The timestamp epoch and health-score thresholds are not yet verified.

An **offline-only** Construct decoder is in
[`research/wifi_diagnostics.py`](research/wifi_diagnostics.py). It parses an
already captured reply, omits the AP BSSID, retains unverified values as raw,
and makes no Bluetooth/network calls. It is not bundled into the released
library or HA integration. Synthetic tests independently pack the layout:

```sh
uv run python -m unittest discover -s research -v
```

**Existing telemetry:** the declarative layout also already contains server IP,
RS485 packet/error/timeout counters and cloud-tether message count. These are
additional device diagnostics, not Bluetooth link-quality measurements. A Wi-Fi
health report will not by itself diagnose an HA adapter/proxy disconnection.

## Evidence and next checks

This session used read-only static analysis and ICMP plus TCP-connect checks on
three standard ports. The device was reachable; the tested TCP ports were refused.
No Bluetooth connection was opened, so HA could continue collecting its logs.
The private connectivity report and decompilation exports remain under ignored
`artifacts/configurator/`.

Next useful checks are a single read of the extended health reply when HA is
not competing for the connection, a credential-redacted Wi-Fi-settings read,
and the router's device-specific DNS/firewall logs. Those logs could establish
which outbound services the device actually attempts while its VLAN is blocked.
They cannot be inferred from the device's historical Server Good value.
