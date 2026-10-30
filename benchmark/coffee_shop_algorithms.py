"""Deterministic logical allocation witnesses for coffee_shop."""
from benchmark.hetero_diagnostic_runtime_v1 import GreedyGoalAlgorithm
class CoffeeShopGoalAlgorithm(GreedyGoalAlgorithm):
    algorithm_id='coffee_shop_contract_witness_v1'
class EmosCoffeeShopAlgorithm(CoffeeShopGoalAlgorithm):
    algorithm_id='EMOS-coffee-shop-goal-v1'
class TeamWeaverCoffeeShopAlgorithm(CoffeeShopGoalAlgorithm):
    algorithm_id='TeamWeaver-coffee-shop-goal-v1'
__all__=['CoffeeShopGoalAlgorithm','EmosCoffeeShopAlgorithm','TeamWeaverCoffeeShopAlgorithm']
