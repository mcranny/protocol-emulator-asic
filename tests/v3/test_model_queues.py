"""The TX and RX queues behave as the V2 engine queues did."""
from protocol_emulator.v3 import model


def test_a_seventeenth_tx_byte_is_rejected() -> None:
    queues = model.Queues()
    for value in range(16):
        queues.push_tx(bytes([value]))
    assert queues.tx_level == 16
    queues.push_tx(b"\xff")
    assert queues.tx_level == 16
    assert [queues.pull() for _ in range(16)] == list(range(16))


def test_a_tx_group_that_does_not_fit_is_rejected_whole() -> None:
    queues = model.Queues()
    queues.push_tx(bytes(14))
    queues.push_tx(b"\x01\x02\x03")
    assert queues.tx_level == 14
    queues.push_tx(b"\x01\x02")
    assert queues.tx_level == 16


def test_an_empty_tx_push_is_ignored() -> None:
    queues = model.Queues()
    queues.push_tx(b"")
    assert queues.tx_level == 0
    assert queues.pull() is None


def test_pulling_from_an_empty_queue_returns_none_and_changes_nothing() -> None:
    queues = model.Queues()
    assert queues.pull() is None
    assert queues.tx_level == 0
    queues.push_tx(b"\x5a")
    assert queues.pull() == 0x5A
    assert queues.pull() is None
    assert queues.tx_level == 0
    queues.push_tx(b"\xa5")
    assert queues.pull() == 0xA5


def test_tx_order_survives_wraparound_of_the_ring() -> None:
    queues = model.Queues()
    expected: list[int] = []
    next_value = 0
    # Repeatedly fill by 3, drain by 2: the pointers lap the 16-entry ring
    # several times without the level ever reaching the extremes.
    for _ in range(40):
        group = bytes((next_value + offset) & 0xFF for offset in range(3))
        next_value += 3
        before = queues.tx_level
        queues.push_tx(group)
        if queues.tx_level != before:
            expected.extend(group)
        for _ in range(2):
            pulled = queues.pull()
            if expected:
                assert pulled == expected.pop(0)
            else:
                assert pulled is None
    while expected:
        assert queues.pull() == expected.pop(0)
    assert queues.pull() is None


def test_rx_records_come_back_in_order_with_their_metadata() -> None:
    queues = model.Queues()
    queues.push_rx(0x11, 0xA0)
    queues.push_rx(0x22, 0xB1)
    assert queues.rx_level == 2
    assert queues.pop_rx() == (0x11, 0xA0)
    assert queues.pop_rx() == (0x22, 0xB1)
    assert queues.pop_rx() is None


def test_rx_values_are_kept_to_eight_bits() -> None:
    queues = model.Queues()
    queues.push_rx(0x1FF, 0x2AA)
    assert queues.pop_rx() == (0xFF, 0xAA)


def test_rx_level_saturates_at_sixteen_and_drops_the_excess() -> None:
    queues = model.Queues()
    for value in range(20):
        queues.push_rx(value, value + 100)
        assert queues.rx_level == min(value + 1, 16)
    for value in range(16):
        assert queues.pop_rx() == (value, value + 100)
    assert queues.pop_rx() is None


def test_rx_order_survives_wraparound_of_the_ring() -> None:
    queues = model.Queues()
    for round_index in range(10):
        for slot in range(5):
            queues.push_rx(round_index, slot)
        for slot in range(5):
            assert queues.pop_rx() == (round_index, slot)
    assert queues.rx_level == 0


def test_reset_empties_both_queues() -> None:
    queues = model.Queues()
    queues.push_tx(b"\x01\x02\x03")
    queues.push_rx(1, 2)
    queues.reset()
    assert queues.tx_level == 0
    assert queues.rx_level == 0
    assert queues.pull() is None
    assert queues.pop_rx() is None


def test_reset_after_wraparound_starts_from_a_clean_ring() -> None:
    queues = model.Queues()
    for _ in range(7):
        queues.push_tx(b"\x01\x02\x03")
        for _ in range(3):
            queues.pull()
    queues.push_tx(b"\x09")
    queues.reset()
    queues.push_tx(b"\x07\x08")
    assert queues.pull() == 7
    assert queues.pull() == 8
    assert queues.pull() is None
