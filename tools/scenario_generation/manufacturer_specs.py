"""Reviewed manufacturer declarations, separate from simulator capabilities.

Product variants are explicit. Similar asset names do not establish a hardware
revision match. These references never replace an experimental payload policy.
"""
from __future__ import annotations

import copy

REVIEWED_ON = "2026-09-15"
SOURCES = {
    "unitree-go2": "https://www.unitree.com/go2/",
    "unitree-b2": "https://www.unitree.com/b2/",
    "unitree-z1": "https://www.unitree.com/z1/",
    "ur5-cb3": "https://www.universal-robots.com/media/50588/ur5_en.pdf",
    "ur5-load-curve": "https://www.universal-robots.com/media/1810042/cb3_deployment_workshop_online_en_1c.pdf",
    "crazyflie-2.1": "https://www.bitcraze.io/documentation/hardware/crazyflie_2_1/crazyflie_2_1-datasheet.pdf",
}
PRODUCTS = {
    "go2_air": {"catalog_key": "go2", "model": "Unitree Go2 AIR", "source": "unitree-go2",
                "payload_kg": 7.0, "relation": "approximately", "condition": "back payload"},
    "go2_pro": {"catalog_key": "go2", "model": "Unitree Go2 PRO", "source": "unitree-go2",
                "payload_kg": 8.0, "relation": "approximately", "condition": "back payload"},
    "go2_x": {"catalog_key": "go2", "model": "Unitree Go2 X", "source": "unitree-go2",
              "payload_kg": 8.0, "relation": "approximately", "condition": "back payload"},
    "go2_edu": {"catalog_key": "go2", "model": "Unitree Go2 EDU", "source": "unitree-go2",
                "payload_kg": 8.0, "relation": "approximately", "condition": "back payload"},
    "b2_walking": {"catalog_key": "b2", "model": "Unitree B2", "source": "unitree-b2",
                   "payload_kg": 40.0, "relation": "greater_than", "condition": "continuous walking"},
    "b2_standing": {"catalog_key": "b2", "model": "Unitree B2", "source": "unitree-b2",
                    "payload_kg": 120.0, "relation": "at_least", "condition": "standing only"},
    "z1_air": {"catalog_key": "z1", "model": "Unitree Z1 AIR", "source": "unitree-z1",
               "payload_kg": 2.0, "relation": "nominal", "condition": "arm end load; test conditions apply"},
    "z1_pro": {"catalog_key": "z1", "model": "Unitree Z1 PRO", "source": "unitree-z1",
               "payload_kg": 3.0, "relation": "at_least", "condition": "arm end load; test conditions apply"},
    "ur5_cb3": {"catalog_key": "ur5", "model": "Universal Robots UR5 CB3", "source": "ur5-cb3",
                "payload_kg": 5.0, "relation": "maximum", "condition": "total tool plus object; CoG curve applies",
                "cog_examples": [{"offset_m": 0.35, "payload_kg": 5.0},
                                 {"offset_m": 0.50, "payload_kg": 3.75}],
                "cog_source": "ur5-load-curve"},
    "crazyflie_2_1": {"catalog_key": "cf2x", "model": "Bitcraze Crazyflie 2.1 (brushed)",
                      "source": "crazyflie-2.1", "payload_kg": 0.015,
                      "relation": "maximum_recommended", "condition": "stock battery; includes added decks"},
}


def product_references(catalog_key):
    """Return source-scoped declarations without inferring asset applicability."""
    records = []
    for product_id, product in PRODUCTS.items():
        if product["catalog_key"] != catalog_key:
            continue
        record = copy.deepcopy(product)
        record.update(product_id=product_id, reviewed_on=REVIEWED_ON,
                      source_url=SOURCES[product["source"]], evidence_tier="E0",
                      asset_variant_match="unverified", simulation_validated=False,
                      gripper_force_N=None, wind_limit_m_s=None)
        if "cog_source" in product:
            record["cog_source_url"] = SOURCES[product["cog_source"]]
        records.append(record)
    return records
