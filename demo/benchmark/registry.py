"""Compatibility registry; each application owns its task definitions."""

from demo.factory_benchmark import scenario as factory
from demo.warehouse_benchmark import scenario as warehouse
from demo.modern_house_benchmark import scenario as modern_house

SCENARIOS = {"factory": factory, "warehouse_nvd": warehouse, "modern_house_nvd": modern_house}
