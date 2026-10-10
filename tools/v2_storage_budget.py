"""Generate a storage-only V2 mapping probe; never a full-design fit claim.

The probe includes two independently read instruction banks, two byte TX
FIFOs, two data/metadata RX FIFOs, and 32 timestamped capture records. It
excludes validity, pointers, engine logic, host transport, clock tree and
physical repair. Map it using the pinned CMOS5L Liberty and retain the log.
"""
import argparse
from pathlib import Path


def verilog(words):
    if words not in (64, 128):
        raise ValueError("program capacity must be 64 or 128")
    address_bits = (words - 1).bit_length()
    blocks = [("program0", words, 24, address_bits), ("program1", words, 24, address_bits),
              ("tx0", 16, 8, 4), ("tx1", 16, 8, 4),
              ("rx0", 16, 16, 4), ("rx1", 16, 16, 4), ("trace", 32, 64, 5)]
    ports = ["input wire clk"]
    body = []
    for name, depth, width, bits in blocks:
        ports += [f"input wire {name}_we", f"input wire [{bits-1}:0] {name}_wa, {name}_ra",
                  f"input wire [{width-1}:0] {name}_d", f"output wire [{width-1}:0] {name}_q"]
        body += [f"reg [{width-1}:0] {name} [0:{depth-1}];",
                 f"always @(posedge clk) if ({name}_we) {name}[{name}_wa] <= {name}_d;",
                 f"assign {name}_q = {name}[{name}_ra];"]
    return "module v2_storage_probe(\n" + ",\n".join(ports) + ");\n" + "\n".join(body) + "\nendmodule\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--words", type=int, choices=(64, 128), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(verilog(args.words))


if __name__ == "__main__":
    main()
