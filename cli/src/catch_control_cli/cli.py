import argparse
import asyncio
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys


async def run(args):
    if args.command == "wifi-listen":
        from .wifi_server import listen
        await listen(args)
        return
    from catch_control.ble import CatchClient, discover
    found = await discover(args.scan_timeout)
    if args.command == "scan":
        print(json.dumps([{"address": d.address, "name": a.local_name or d.name,
                           "rssi": a.rssi, "model": 10004} for d, a in found], indent=2))
        return
    candidates = [d for d, _ in found if not args.address or d.address.lower() == args.address.lower()]
    if len(candidates) != 1:
        raise RuntimeError(f"Found {len(candidates)} matching Catch devices; run scan and select --address. "
                           "If the phone is connected, disconnect it in Configurator first.")
    async with CatchClient(candidates[0], timeout=args.timeout) as client:
        if args.command == "identity":
            print(json.dumps(asdict(client.identity), indent=2))
            return
        if args.command == "configuration":
            print(json.dumps(await client.configuration(), indent=2))
            return
        if args.command == "wifi-setup":
            password = read_password(args.password_file)
            plan = await client.plan_websocket_server(
                args.host, args.port, password=password
            )
            result = await client.apply_websocket_server(plan, password=password) if args.apply else plan.summary()
            result = {
                **result,
                'before': asdict(result['before']),
                'after': asdict(result['after']),
            }
            print(json.dumps(result, indent=2))
            return
        if args.command == "schedule":
            from catch_control.configuration import Schedule
            from catch_control.enums import ControlMode
            password = read_password(args.password_file)
            schedule = Schedule(args.active == 'yes', ControlMode[args.mode.upper()], args.start, args.stop)
            plan = await client.plan_schedule(args.slot, schedule, password=password)
            result = await client.apply_schedule(plan, password=password) if args.apply else plan.summary()
            print(json.dumps(result, indent=2))
            return
        samples = 0
        while True:
            print(json.dumps(await client.telemetry()), flush=True)
            samples += 1
            if args.command != "watch" or (args.count and samples >= args.count):
                return
            await asyncio.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description="Local CATCH Control 2CH client")
    parser.add_argument("command", choices=["scan", "identity", "configuration", "read", "watch", "schedule", "wifi-setup", "wifi-listen"])
    parser.add_argument("--address", help="Bluetooth address (CoreBluetooth UUID on macOS)")
    parser.add_argument("--scan-timeout", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--count", type=int, help="Stop watch after this many samples")
    parser.add_argument("--slot", type=int, choices=range(1, 5))
    parser.add_argument("--active", choices=['yes', 'no'])
    parser.add_argument("--mode", choices=['default', 'export', 'turn_on', 'turn_off', 'top_up', 'voltage', 'frequency'])
    parser.add_argument("--start", type=parse_time, help="Schedule start, HH:MM in device local time")
    parser.add_argument("--stop", type=parse_time, help="Schedule stop, HH:MM in device local time")
    parser.add_argument("--password-file", help="Private file containing the device password")
    parser.add_argument("--apply", action='store_true', help="Apply the schedule change; otherwise only preview it")
    parser.add_argument("--bind", default="127.0.0.1", help="Address for wifi-listen (default: loopback)")
    parser.add_argument("--port", type=int, default=8443, help="TLS port for wifi-listen")
    parser.add_argument("--cert", help="PEM certificate chain for wifi-listen")
    parser.add_argument("--key", help="PEM private key for wifi-listen")
    parser.add_argument("--host", help="DNS name or IPv4 address advertised to the CATCH")
    args = parser.parse_args()
    if any(not math.isfinite(x) or x <= 0 for x in (args.scan_timeout, args.timeout, args.interval)):
        parser.error("Timeouts and polling interval must be finite and positive")
    if args.count is not None and (args.command not in ('watch', 'wifi-listen') or args.count < 1):
        parser.error("--count requires watch or wifi-listen and a positive integer")
    wifi_args = [args.cert, args.key]
    if args.command == 'wifi-listen':
        if any(value is None for value in wifi_args):
            parser.error("wifi-listen requires --cert and --key")
        if not 1024 <= args.port <= 65535:
            parser.error("wifi-listen port must be between 1024 and 65535")
    elif any(value is not None for value in wifi_args) or args.bind != '127.0.0.1':
        parser.error("--bind, --port, --cert and --key require wifi-listen")
    schedule_args = [args.slot, args.active, args.mode, args.start, args.stop, args.password_file]
    if args.command == 'schedule':
        if any(value is None for value in schedule_args):
            parser.error("schedule requires --slot, --active, --mode, --start, --stop and --password-file")
    elif args.command == 'wifi-setup':
        if args.host is None or args.password_file is None:
            parser.error("wifi-setup requires --host, --port and --password-file")
        if not 1 <= args.port <= 32767:
            parser.error("wifi-setup port must be between 1 and 32767")
        if any(value is not None for value in schedule_args[:-1]):
            parser.error("Schedule fields require the schedule command")
    elif (args.apply or args.host is not None or any(value is not None for value in schedule_args)
          or (args.command != 'wifi-listen' and args.port != 8443)):
        parser.error("Schedule options require the schedule command")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"catch-control: {exc}", file=sys.stderr)
        return 1
    return 0


def parse_time(value):
    try:
        hour, minute = value.split(':')
        if len(hour) != 2 or len(minute) != 2 or not hour.isascii() or not minute.isascii():
            raise ValueError
        hour, minute = int(hour), int(minute)
        if not 0 <= hour < 24 or not 0 <= minute < 60:
            raise ValueError
        return hour * 60 + minute
    except ValueError:
        raise argparse.ArgumentTypeError("Use a time from 00:00 to 23:59") from None


def read_password(filename):
    path = Path(filename)
    if path.stat().st_mode & 0o077:
        raise ValueError("Password file must be accessible only to its owner (chmod 600)")
    if path.stat().st_size > 1024:
        raise ValueError("Password file is unexpectedly large")
    return path.read_text().rstrip('\r\n')
