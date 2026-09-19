"""Command-line tools. Binary images contain big-endian 24-bit words."""
import argparse
import json
from pathlib import Path
from .isa import assemble, disassemble, image_bytes, image_words
from .host import Device, DeviceError, SimulationTransport, SpiTransport


def main(argv=None):
    parser = argparse.ArgumentParser(prog="protocol-emulator")
    parser.add_argument("--transport", choices=("sim", "spi"), default="sim")
    parser.add_argument("--bus", type=int, default=0)
    parser.add_argument("--device", type=int, default=0)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("assemble")
    p.add_argument("source", type=Path)
    p.add_argument("output", type=Path)
    p = sub.add_parser("disassemble")
    p.add_argument("image", type=Path)
    p = sub.add_parser("run", help="load, verify, enqueue, and execute in one session")
    p.add_argument("image", type=Path)
    p.add_argument("--data", default="")
    p.add_argument("--cycles", type=int, default=10000)
    for command in ("load", "verify"):
        p = sub.add_parser(command)
        p.add_argument("image", type=Path)
    p = sub.add_parser("fifo-write")
    p.add_argument("data", help="hexadecimal byte sequence")
    for command in ("start", "stop", "status", "reset", "version"):
        sub.add_parser(command)
    args = parser.parse_args(argv)
    transport = None
    try:
        if args.command == "assemble":
            args.output.write_bytes(image_bytes(assemble(args.source.read_text())))
            return 0
        if args.command == "disassemble":
            print(disassemble(image_words(args.image.read_bytes())), end="")
            return 0
        transport = SimulationTransport() if args.transport == "sim" else SpiTransport(args.bus, args.device)
        device = Device(transport)
        if args.command in ("load", "verify"):
            getattr(device, args.command)(image_words(args.image.read_bytes()))
        elif args.command == "fifo-write":
            device.fifo_write(bytes.fromhex(args.data))
        elif args.command == "status":
            print(f"0x{device.status():06x}")
        elif args.command == "version":
            print(f"0x{device.request(9):06x}")
        elif args.command == "run":
            if args.cycles < 0:
                raise ValueError("cycles must be nonnegative")
            payload = bytes.fromhex(args.data)
            if len(payload) > 4:
                raise ValueError("run preloads at most four bytes")
            device.load(image_words(args.image.read_bytes()))
            device.fifo_write(payload)
            device.start()
            if isinstance(transport, SimulationTransport):
                for _ in range(args.cycles):
                    transport.model.step()
                print(json.dumps(transport.model.snapshot(), sort_keys=True))
        else:
            getattr(device, args.command)()
        return 0
    except (OSError, ValueError, RuntimeError, DeviceError) as exc:
        parser.exit(2, f"error: {exc}\n")
    finally:
        if isinstance(transport, SpiTransport):
            transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
