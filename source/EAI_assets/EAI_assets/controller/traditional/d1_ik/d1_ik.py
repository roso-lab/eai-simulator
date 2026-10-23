from __future__ import annotations

from EAI.hmrs_ros.d1_omnigraph import D1_MODEL_SPEC
from EAI_assets.controller.traditional.manipulator_ik.manipulator_ik import ManipulatorIkControllerCfg


D1IkControllerCfg = ManipulatorIkControllerCfg


D1_IK_CFG = D1IkControllerCfg(model_spec=D1_MODEL_SPEC)
