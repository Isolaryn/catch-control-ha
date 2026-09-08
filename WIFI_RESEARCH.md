# Wi-Fi control and diagnostics investigation

Updated 2026-09-08. App, firmware, packet captures and device-specific settings
stay private; the independently implemented protocol layouts are public.

## What is established

| Route | Evidence | Current conclusion |
| --- | --- | --- |
| Direct HTTP/HTTPS on CATCH | Device answers ICMP; TCP connections to 80 and 443 were refused in this session | No web API at those ports at the time checked; other ports/protocols are not ruled out |
| Direct Modbus/TCP on CATCH | TCP 502 refused; vendor documents configure port 502 on the inverter | Published Modbus/TCP support does not establish an inbound CATCH control server |
| Outbound WebSocket | Firmware has bidirectional dispatch; device completed WSS with RSA TLS and answered telemetry/configuration requests plus a reversible schedule write | Local metrics and single-schedule control are verified on firmware 12718 |
| Wi-Fi diagnostics over BLE | App/firmware layout matched a live extended health reply | Diagnostic path works independently of a local Wi-Fi control API; capability and collection state matter |

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
and an outbound wrapper sends responses through the WebSocket client. Telemetry,
configuration reads, and one validated schedule-record write are implemented.

The client constructs an outbound `wss://<host>:<port>/srwe` connection. Primary
and secondary hosts and ports come from mutable Wi-Fi settings. Configurator's
Wi-Fi setup code preserves those fields when changing SSID/credentials.
The hostnames are therefore configuration, not proof of a fixed cloud endpoint.

Primary and secondary server settings were temporarily changed over Bluetooth
and verified by readback, then restored and verified after each bounded test.
RSA certificate compatibility and the request/response protocol were then
verified with a temporary owner-controlled endpoint. Firmware was not flashed,
and no device trust settings were changed.

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

The matching ESP-IDF 4.4.7 TLS implementation can begin a client handshake with
all of those verification inputs absent only when its compile-time
`CONFIG_ESP_TLS_SKIP_SERVER_CERT_VERIFY` option is enabled; that path configures
mbedTLS with `VERIFY_NONE`. The live ClientHello therefore confirms that this
firmware does not authenticate the server certificate in this path. It still
sends the configured hostname as SNI. A self-signed RSA certificate is therefore
sufficient; no root installation is needed. This is a device security limitation,
so network isolation of the listener matters.

The contents of uninitialized image memory were not treated as runtime values;
these conclusions follow from the initialization writes, matching upstream
control flow and the live handshake. The live device accepted protocol requests
immediately after upgrade, with no additional application login or handshake.

The Home Assistant implementation generates and retains a long-lived RSA
self-signed certificate. The listener should remain reachable only from the
intended IoT network because the device does not authenticate it.

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
merely finding a diagnostic string. A subsequent live read confirmed the layout
and returned gateway-RTT and application-disconnect capability flags alongside
collected signal/latency measurements. A reply soon after restarting Wi-Fi had
fewer capability flags and zero collection timestamp/measurements. Those values
must not be interpreted as a fresh healthy-link report.

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

Initial investigation used read-only static analysis and ICMP plus TCP-connect checks on
three standard ports. The device was reachable; the tested TCP ports were refused.
Bluetooth was initially left free so HA could collect logs; subsequent device
tests ran after the owner released Bluetooth from HA and Configurator.
The private connectivity report and decompilation exports remain under ignored
`artifacts/configurator/`.

Wi-Fi settings and health replies have now been read and backed up privately.
Further useful evidence includes device-side TLS errors and the router's
device-specific DNS/firewall logs. Those logs could establish
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
An owner-controlled endpoint was subsequently checked through a temporary DMZ
TCP relay and SSH reverse tunnel using a normally issued certificate. A synthetic
client passed CA and hostname validation, negotiated TLS 1.3, completed the
WebSocket upgrade and exchanged ping/pong without application messages. The
listener, tunnel and relay were configured to stop after 30 minutes. Host-specific
details and certificate material remain in ignored local files. This validates
the endpoint from that client, not reachability or TLS compatibility from CATCH.
Before a device test, save current settings privately and establish how they will
be restored; endpoint compatibility and application authentication remain unverified.

## Bounded device endpoint tests

After fixing name resolution on the device's configured DNS resolver, bounded
tests temporarily pointed both server entries at the owner endpoint. Each test
authenticated the device, checked a fresh settings read against its
private backup, preserved all fields except the two hosts and ports, and verified
the write. Each then restored all 228 Wi-Fi settings bytes and verified that the
stable control configuration still matched its backup.

With an ECDSA certificate, the device established TCP connections and sent a TLS 1.2 ClientHello. Passive
handshake decoding with Scapy identified a TLS 1.2 ServerHello selecting
`TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384`, followed by four certificates, an X25519
server key exchange and ServerHelloDone. The captured client traffic did not
continue the handshake. No device HTTP upgrade or WebSocket application message
was observed. A second test increased the listener opening timeout from 10 to
60 seconds with the same certificate; it did not resolve the failure.

Replacing that endpoint certificate with a separately issued RSA-2048 certificate
for the same hostname changed the result. A normal client first passed CA and
hostname validation through the same route, negotiating TLS 1.2 with
`ECDHE-RSA-AES256-GCM-SHA384`. Firmware 12718 then completed TLS and the `/srwe`
WebSocket upgrade. This isolates the observed incompatibility to the ECDSA
certificate/handshake path rather than routing, DNS, TLS version, certificate
pinning or a required CA root. It does not identify the precise ECDSA failure.
The current Let's Encrypt certificate hierarchy is documented in its
[published chain information](https://letsencrypt.org/certificates/).

A subsequent test replaced the issued chain with a ten-year, self-signed
RSA-2048 certificate matching the Home Assistant generator. The device again
completed TLS 1.2 and the WebSocket upgrade and returned a valid 145-byte
telemetry response. Its original server settings and stable control configuration
were restored and verified afterward.

The RSA connection remained silent until the server sent the statically identified,
read-only telemetry request. The device answered in about 160 ms with exactly 145
binary bytes. That message parsed with the existing Construct `TELEMETRY_FIELDS`
layout and contained the expected model/firmware plus plausible voltage and
frequency. It is the BLE telemetry structure without BLE framing, payload padding
or CRC. No message was observed to be pushed spontaneously during the passive
window. This confirms local Wi-Fi metrics and request/response behavior.

The WebSocket `GETCFG` response is the firmware's exact 128-byte internal
control structure. Its four schedule records are 7-byte packed structures at
offsets 76, 83, 90 and 97. They use the same active/mode/start/stop representation
as the Bluetooth configuration projection. The write command contains an
offset, length and replacement data. Firmware explicitly validates a 7-byte
write beginning at each schedule offset before copying and persisting it.

A bounded test read all schedules, changed the mode of inactive slot 4 using
that single-record operation, checked the acknowledgement and full schedule
readback, then restored the original record and verified it again. A final
authenticated Bluetooth read confirmed the original stable configuration and
server settings. This is the control operation exposed by the library and Home
Assistant Wi-Fi mode; arbitrary configuration writes are not exposed.

Temporary DMZ relays, tunnels and listeners were stopped after restoration.
Hostnames, addresses, credentials, certificate material, original settings and
captures remain private and ignored. The public library, CLI listener and Home
Assistant integration contain no private endpoint, device or capture details.
