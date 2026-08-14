from __future__ import annotations

import pytest

from TeamWeaver.tests.eai_test_support import make_factory_world


@pytest.fixture
def factory_world():
    return make_factory_world()
