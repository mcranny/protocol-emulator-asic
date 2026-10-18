"""Simulation helpers for v2 pins."""

from cocotb.triggers import Timer
from protocol_emulator.v2.host import command_frame, parse_response
from protocol_emulator.v2.isa import assemble


async def capture_words(host):
    status = await host.request(0x32)
    assert not status & 1
    count = status >> 6 & 63
    requests = [
        command_frame(command, address=i)
        for i in range(count)
        for command in (0x35, 0x36, 0x37)
    ]
    chunks = []
    for offset in range(0, len(requests), 64):
        chunks += await batch(host, requests[offset : offset + 64])
    return status, [
        chunks[i] | chunks[i + 1] << 24 | chunks[i + 2] << 48
        for i in range(0, len(chunks), 3)
    ]


async def batch(host, requests, allow_empty=False):
    requests = list(requests)
    result = []
    previous = None
    for frame in requests + [bytes(5)]:
        response = await host.exchange(
            frame[0], frame[1], int.from_bytes(frame[2:], "big")
        )
        if previous is not None:
            result.append(
                parse_response(
                    previous, response.to_bytes(5, "big"), allow_empty=allow_empty
                )
            )
        previous = frame
    return result


async def load(host, engine, source):
    words = assemble(source, capacity=64)
    await batch(
        host, [command_frame(1, engine, i, word) for i, word in enumerate(words)]
    )
    assert (
        await batch(host, [command_frame(2, engine, i) for i in range(len(words))])
        == words
    )


async def send_uart(dut, data, period=217):
    dut.uio_in.value = 255
    await Timer(123, unit="ns")
    for value in data:
        for bit in [0] + [value >> i & 1 for i in range(8)] + [1]:
            dut.uio_in.value = 253 | bit << 1
            await Timer(period * 40, unit="ns")
    dut.uio_in.value = 255
