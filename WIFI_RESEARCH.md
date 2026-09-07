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
The examined dispatcher handles telemetry and configuration/control operations,
and an outbound wrapper sends responses through the WebSocket client. This is
a bidirectional application transport; safe self-hosted control is not yet
implemented or tested.

The client constructs an outbound `wss://<host>:<port>/srwe` connection. Primary
and secondary hosts and ports come from mutable Wi-Fi settings. Configurator's
Wi-Fi setup code preserves those fields when changing SSID/credentials.
The hostnames are therefore configuration, not proof of a fixed cloud endpoint.

Changing the server settings, certificate compatibility, application handshake,
session authentication and safe control through a self-hosted endpoint remain
unverified. DNS redirection alone would not establish TLS or protocol compatibility.
No redirect, certificate change, listener deployment or device setting change was
made in this investigation. Firmware was not flashed.

### TLS and authentication evidence

The URI builder fixes the scheme to `wss`, so changing the configured host and
port does not select plaintext WebSocket transport.

The application initialization was compared with the matching
[ESP-IDF 4.4.7 client configuration definition](https://raw.githubusercontent.com/espressif/esp-idf/v4.4.7/components/esp_websocket_client/include/esp_websocket_client.h)
and [client initialization source](https://github.com/espressif/esp-idf/blob/v4.4.7/components/esp_websocket_client/esp_websocket_client.c).
It clears the configuration after its URI pointer, assigns that pointer, and
enables the flags disabling automatic reconnect and ping/pong disconnection.
Those flags do not enable a trust store or certificate pinning.

In this initialization path:

- No server CA certificate is supplied, and the global CA store flag is false.
- No client certificate or private key is supplied for mutual TLS.
- No HTTP username, password, additional headers or subprotocol is supplied.
- No explicit certificate or public-key pin was identified in the examined
  application connection path.

This is static evidence against a specifically configured root or pin in that
path, not a complete audit of the lower TLS stack or a live certificate-acceptance
result. The contents of uninitialized image memory were not treated as runtime
values; these conclusions follow from the initialization writes. Application
session authentication remains unresolved and is separate from TLS and HTTP
authentication. No invalid-certificate, impersonation or control-command test
was performed.

A prospective owner-configured endpoint should use TLS with a certificate valid
for its hostname. Initial diagnostics should observe connection success and
message metadata without sending application control messages or logging
credentials. Certificate compatibility and the application session still need
to be established before describing this as an operational local integration.

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
uv run --with websockets==17.0.1 python -m unittest discover -s research -v
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

## Passive TLS endpoint preparation

[`research/passive_wss.py`](research/passive_wss.py) is a temporary observer,
separate from the released packages. It binds to laptop loopback, requires a
certificate/key, accepts `/srwe`, and stops after ten minutes by default. It
records TLS version, message type, byte count and disconnect status. Headers,
payloads, peer identifiers and close-reason text are not logged. It sends no
application messages; the WebSocket library handles protocol ping/pong and close.
Silence after connection cannot establish lack of telemetry: the application
may expect a server handshake or subscription that this observer does not send.

Run with the certificate chain valid for the chosen endpoint hostname and its
private key stored under ignored `.secrets/`:

```sh
uv run research/passive_wss.py --cert .secrets/test-fullchain.pem --key .secrets/test-key.pem
```

An owner-controlled DMZ host can forward a chosen unprivileged TCP port back to
this listener. With `DMZ_BIND_IP` and `DMZ_SSH_TARGET` set for that host:

```sh
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -R "${DMZ_BIND_IP}:8443:127.0.0.1:8443" "$DMZ_SSH_TARGET"
```

The host's SSH policy must permit binding the requested DMZ address, rather
than forcing the remote listener onto loopback. Its firewall should restrict
access to the intended IoT source. Confirm the actual bind and end-to-end TLS
reachability before configuring a device. The endpoint hostname must resolve
to the DMZ address from the IoT network. TLS terminates at the laptop through
the TCP tunnel; the private key need not be copied to the DMZ host.
These behaviors follow the [OpenSSH remote-forwarding documentation](https://man.openbsd.org/ssh#R)
and the [websockets server API](https://websockets.readthedocs.io/en/stable/reference/asyncio/server.html).

The observer has passed loopback integration tests using an ephemeral test
certificate explicitly trusted by the synthetic client, including metadata-only
logging, no application reply, ping/pong and rejection of other paths. That
certificate is only a local test fixture and has not been presented to CATCH.
No listener or tunnel has yet been deployed on a DMZ host, and no CATCH server
settings have been changed. Before a device test, save its current settings
privately and establish how they will be restored; endpoint compatibility and
application authentication remain unverified.
