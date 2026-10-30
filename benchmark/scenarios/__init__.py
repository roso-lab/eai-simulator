"""Lazy scene exports; optional scene dependencies fail at their own import."""
from importlib import import_module

_MODULES = {'City01ScenarioAdapter': 'city_01', 'City02ScenarioAdapter': 'city_02',
            'CandyShopScenarioAdapter': 'candy_shop', 'Room06ScenarioAdapter': 'room_06',
            'LivingHouseScenarioAdapter': 'living_house', 'BarScenarioAdapter': 'bar'}
__all__ = list(_MODULES)


def __getattr__(name):
    if name not in _MODULES:
        raise AttributeError(name)
    value = getattr(import_module('.' + _MODULES[name], __name__), name)
    globals()[name] = value
    return value
