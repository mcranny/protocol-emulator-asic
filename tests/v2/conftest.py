"""Function-scoped factories for V2 behavior tests."""

import pytest
from protocol_emulator.v2.isa import assemble
from protocol_emulator.v2.model import Engine


@pytest.fixture
def make_engine():
    def create(source, owner=0):
        e = Engine()
        e.owner = owner
        e.load(assemble(source))
        e.start()
        return e

    return create
