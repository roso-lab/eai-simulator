"""Reusable scene adapters for the diagnostic runtime."""

try:
    from .city_01 import City01ScenarioAdapter
except ModuleNotFoundError:
    City01ScenarioAdapter = None
try:
    from .city_02 import City02ScenarioAdapter
except ModuleNotFoundError:
    City02ScenarioAdapter = None
try:
    from .candy_shop import CandyShopScenarioAdapter
except ModuleNotFoundError:
    CandyShopScenarioAdapter = None
try:
    from .room_06 import Room06ScenarioAdapter
except ModuleNotFoundError:
    Room06ScenarioAdapter = None
from .bar import BarScenarioAdapter

__all__ = ["City01ScenarioAdapter", "City02ScenarioAdapter", "CandyShopScenarioAdapter", "Room06ScenarioAdapter", "BarScenarioAdapter"]
