"""Order-independent random streams used by benchmark noise and transport."""

import numpy as np

from EAI.keyed_random import KeyedRandom, keyed_seed
from EAI.physics.aerial_sensors import AerialSensorModel


def _sample(model, event_key):
    return model.sample(
        position_w=(0.0, 0.0, 1.0),
        orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
        linear_velocity_w=(0.1, 0.0, 0.0),
        angular_velocity_b=(0.0, 0.0, 0.01),
        dt=0.02,
        event_key=event_key,
    )


def test_keyed_draw_is_unchanged_by_unrelated_branch_draws():
    left = KeyedRandom(17, "communication")
    right = KeyedRandom(17, "communication")
    expected = left.normal("robot-a", "pose:12", size=4)
    right.normal("robot-b", "extra-probe", size=99)
    actual = right.normal("robot-a", "pose:12", size=4)
    np.testing.assert_array_equal(expected, actual)


def test_keyed_seed_does_not_depend_on_roster_order():
    assert keyed_seed(7, "aerial_sensor", "go2_1") == keyed_seed(7, "aerial_sensor", "go2_1")
    assert keyed_seed(7, "aerial_sensor", "go2_1") != keyed_seed(7, "aerial_sensor", "go2_2")


def test_sensor_event_key_is_replay_stable_after_unrelated_draw():
    baseline = AerialSensorModel(seed=5)
    branched = AerialSensorModel(seed=5)
    first = _sample(baseline, ("physics_step", 3))
    branched._rng.normal("other", ("physics_step", 3), size=100)
    second = _sample(branched, ("physics_step", 3))
    np.testing.assert_array_equal(first.angular_velocity_body, second.angular_velocity_body)
    np.testing.assert_array_equal(first.linear_acceleration_body, second.linear_acceleration_body)
    assert first.latitude_deg == second.latitude_deg
