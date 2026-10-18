"""Function-scoped synthetic evidence factories."""

import pytest

from tests.support.evidence import build_physical_evidence


@pytest.fixture
def make_physical_evidence(tmp_path):
    return lambda: build_physical_evidence(tmp_path)
