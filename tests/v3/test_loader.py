"""The loader writes, reads back, configures and starts through a one-frame-late reply."""
import pytest

from protocol_emulator.v3 import PROGRAM_WORDS, host, loader
from protocol_emulator.v3.isa import assemble

CMD = host.COMMANDS


class FakeDevice:
    """A device model that replies to each frame on the following transfer."""

    def __init__(self) -> None:
        self.memory: list[list[int]] = [[0] * PROGRAM_WORDS, [0] * PROGRAM_WORDS]
        self.running = [False, False]
        self.config: dict[int, host.Frame] = {}
        self.watches: list[host.Frame] = []
        self.sent: list[host.Frame] = []
        self.version = host.VERSION_WORD
        self.corrupt_read: dict[int, int] = {}
        self._pending = host.Frame(CMD["NOP"], 0, 0)

    def exchange(self, request: host.Frame) -> host.Frame:
        self.sent.append(request)
        reply = self._pending
        self._pending = self._execute(request)
        return reply

    def _execute(self, request: host.Frame) -> host.Frame:
        code = request.cmd & ~host.ENGINE_BIT
        engine = 1 if request.cmd & host.ENGINE_BIT else 0
        payload = 0
        if code == CMD["WRITE"]:
            if self.running[engine]:
                return host.Frame(request.cmd | host.REJECT_BIT, request.addr, 0)
            self.memory[engine][request.addr] = request.payload
        elif code == CMD["READ"]:
            payload = self.corrupt_read.get(request.addr, self.memory[engine][request.addr])
        elif code == CMD["CONFIG"]:
            self.config[engine] = request
        elif code == CMD["WATCH"]:
            self.watches.append(request)
        elif code == CMD["START"]:
            for index in range(2):
                if request.payload >> index & 1:
                    self.running[index] = True
        elif code == CMD["STOP"]:
            self.running = [False, False]
        elif code == CMD["VERSION"]:
            payload = self.version
        return host.Frame(request.cmd, request.addr, payload)


@pytest.fixture
def device() -> FakeDevice:
    return FakeDevice()


def test_connect_accepts_the_v3_version_word(device: FakeDevice) -> None:
    loader.connect(device.exchange)
    assert [f.cmd for f in device.sent][0] == CMD["VERSION"]


def test_connect_refuses_another_version_and_reports_it(device: FakeDevice) -> None:
    device.version = 0x02024000
    with pytest.raises(loader.ProtocolError, match="0x02024000"):
        loader.connect(device.exchange)


def test_load_writes_every_word_of_the_image(device: FakeDevice) -> None:
    image = assemble("LI reg=0 value=5\nHALT")
    loader.load(device.exchange, 0, image)
    assert device.memory[0] == list(image.words)
    assert device.memory[1] == [0] * PROGRAM_WORDS
    writes = [f for f in device.sent if f.cmd == CMD["WRITE"]]
    assert [f.addr for f in writes] == list(range(PROGRAM_WORDS))


def test_load_reads_the_whole_image_back(device: FakeDevice) -> None:
    loader.load(device.exchange, 0, assemble("HALT"))
    reads = [f for f in device.sent if f.cmd == CMD["READ"]]
    assert [f.addr for f in reads] == list(range(PROGRAM_WORDS))


def test_load_names_the_first_mismatching_address(device: FakeDevice) -> None:
    image = assemble("LI reg=0 value=5\nHALT")
    device.corrupt_read = {17: 0xDEADBEEF, 40: 0x12345678}
    expected = f"0x{image.words[17]:08X}"
    with pytest.raises(loader.ProtocolError, match="address 17") as info:
        loader.load(device.exchange, 0, image)
    assert expected in str(info.value)
    assert "0xDEADBEEF" in str(info.value)
    assert "address 40" not in str(info.value)


def test_load_to_engine_1_sets_the_engine_bit_on_every_write_and_read(
    device: FakeDevice,
) -> None:
    image = assemble("HALT")
    loader.load(device.exchange, 1, image)
    data = [f for f in device.sent if f.cmd & ~host.ENGINE_BIT in (CMD["WRITE"], CMD["READ"])]
    assert len(data) == 2 * PROGRAM_WORDS
    assert all(f.cmd & host.ENGINE_BIT for f in data)
    assert device.memory[1] == list(image.words)
    assert device.memory[0] == [0] * PROGRAM_WORDS


def test_load_to_engine_0_never_sets_the_engine_bit(device: FakeDevice) -> None:
    loader.load(device.exchange, 0, assemble("HALT"))
    assert not any(f.cmd & host.ENGINE_BIT for f in device.sent)


def test_a_rejected_reply_raises_protocol_error(device: FakeDevice) -> None:
    device.running[0] = True
    with pytest.raises(loader.ProtocolError, match="WRITE"):
        loader.load(device.exchange, 0, assemble("HALT"))


def test_start_sends_one_frame_with_the_engine_mask(device: FakeDevice) -> None:
    loader.start(device.exchange, [0, 1])
    starts = [f for f in device.sent if f.cmd == CMD["START"]]
    assert starts == [host.Frame(CMD["START"], 0, 0b11)]
    assert device.running == [True, True]


def test_start_of_one_engine_sets_only_its_bit(device: FakeDevice) -> None:
    loader.start(device.exchange, [1])
    assert [f.payload for f in device.sent if f.cmd == CMD["START"]] == [0b10]


def test_configure_sends_config_then_one_watch_per_entry(device: FakeDevice) -> None:
    loader.configure(device.exchange, 0, 0x0F, 0x03, [(0x01, 0x00, 0x02), (0xF0, 0x10, 0x01)])
    commands = [f for f in device.sent if f.cmd != CMD["NOP"]]
    assert commands == [
        host.Frame(CMD["CONFIG"], 0, 0x0F | 0x03 << 8),
        host.Frame(CMD["WATCH"], 0, 0x01 | 0x00 << 8 | 0x02 << 16),
        host.Frame(CMD["WATCH"], 1, 0xF0 | 0x10 << 8 | 0x01 << 16),
    ]


def test_configure_without_watches_sends_only_config(device: FakeDevice) -> None:
    loader.configure(device.exchange, 0, 0x0F, 0x00)
    assert [f.cmd for f in device.sent if f.cmd != CMD["NOP"]] == [CMD["CONFIG"]]


def test_configure_sets_the_abort_bit_for_an_abort_watcher(device: FakeDevice) -> None:
    watches: list[tuple[int, ...]] = [(0x01, 0x01, 0x02, True), (0xF0, 0x10, 0x01)]
    loader.configure(device.exchange, 0, 0x0F, 0x00, watches)
    sent = [f for f in device.sent if f.cmd & ~host.ENGINE_BIT == CMD["WATCH"]]
    assert sent[0].payload == (0x01 | 0x01 << 8 | 0x02 << 16 | 1 << 24)
    assert sent[1].payload == (0xF0 | 0x10 << 8 | 0x01 << 16), "no abort bit on a 3-tuple"


def test_a_watch_tuple_of_the_wrong_size_is_refused(device: FakeDevice) -> None:
    bad: list[tuple[int, ...]] = [(1, 2)]
    with pytest.raises(ValueError, match="watch"):
        loader.configure(device.exchange, 0, 0, 0, bad)
    assert device.sent == []


def test_configure_engine_1_sets_the_engine_bit(device: FakeDevice) -> None:
    loader.configure(device.exchange, 1, 0xF0, 0x00, [(1, 1, 1)])
    commands = [f for f in device.sent if f.cmd & ~host.ENGINE_BIT != CMD["NOP"]]
    assert all(f.cmd & host.ENGINE_BIT for f in commands)
    assert 1 in device.config


def test_transact_aligns_replies_with_requests_and_sends_one_extra_frame(
    device: FakeDevice,
) -> None:
    device.memory[0][3] = 0xAAAA0003
    device.memory[0][9] = 0xBBBB0009
    requests = [
        host.Frame(CMD["READ"], 3, 0),
        host.Frame(CMD["READ"], 9, 0),
        host.Frame(CMD["VERSION"], 0, 0),
    ]
    replies = loader.transact(device.exchange, requests)
    assert [r.payload for r in replies] == [0xAAAA0003, 0xBBBB0009, host.VERSION_WORD]
    assert len(device.sent) == len(requests) + 1
    assert device.sent[-1].cmd == CMD["NOP"]


def test_transact_discards_the_lagging_reply_of_an_earlier_session(
    device: FakeDevice,
) -> None:
    loader.transact(device.exchange, [host.Frame(CMD["VERSION"], 0, 0)])
    replies = loader.transact(device.exchange, [host.Frame(CMD["READ"], 0, 0)])
    assert [r.cmd for r in replies] == [CMD["READ"]]


def test_transact_rejects_a_reply_that_does_not_echo_its_request() -> None:
    def wrong(request: host.Frame) -> host.Frame:
        return host.Frame(request.cmd, request.addr ^ 1, 0)

    with pytest.raises(loader.ProtocolError, match="READ"):
        loader.transact(wrong, [host.Frame(CMD["READ"], 4, 0), host.Frame(CMD["READ"], 5, 0)])


def test_transact_with_no_requests_sends_one_nop(device: FakeDevice) -> None:
    assert loader.transact(device.exchange, []) == []
    assert device.sent == [host.Frame(CMD["NOP"], 0, 0)]


def test_a_bad_engine_is_refused(device: FakeDevice) -> None:
    image = assemble("HALT")
    with pytest.raises(ValueError, match="engine"):
        loader.load(device.exchange, 2, image)
    with pytest.raises(ValueError, match="engine"):
        loader.configure(device.exchange, 2, 0, 0)
    assert device.sent == []


def test_a_nine_bit_owner_mask_is_refused(device: FakeDevice) -> None:
    with pytest.raises(ValueError, match="owner"):
        loader.configure(device.exchange, 0, 0x100, 0)
    with pytest.raises(ValueError, match="open_drain"):
        loader.configure(device.exchange, 0, 0, 0x100)
    assert device.sent == []


def test_three_watches_are_refused(device: FakeDevice) -> None:
    with pytest.raises(ValueError, match="watch"):
        loader.configure(device.exchange, 0, 0, 0, [(0, 0, 0)] * 3)
    assert device.sent == []


def test_a_wide_watch_field_is_refused(device: FakeDevice) -> None:
    with pytest.raises(ValueError, match="watch"):
        loader.configure(device.exchange, 0, 0, 0, [(0x100, 0, 0)])
    assert device.sent == []


@pytest.mark.parametrize("engines", [[], [2], [-1], [0, 2]])
def test_an_empty_or_out_of_range_start_set_is_refused(
    device: FakeDevice, engines: list[int]
) -> None:
    with pytest.raises(ValueError, match="engine"):
        loader.start(device.exchange, engines)
    assert device.sent == []
