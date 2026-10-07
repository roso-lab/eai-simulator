from __future__ import annotations

import pytest

from algorithm.system.coordination.TeamWeaver.tests.eai_test_support import make_factory_world


@pytest.fixture
def factory_world():
    return make_factory_world()
