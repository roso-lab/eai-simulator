"""Official NVIDIA Flow presets adapted to the owning metre/Z-up stage.

Emitter transforms and FlowSimulate settings have different USD semantics:
densityCellSize is in WORLD units, and advection gravity is world-oriented.
Use native metre coordinates rather than a cm/Y-up conversion parent.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from demo.city.scenario import AccidentState

CM_TO_M = 0.01
FLOW_KIT_PRELOAD_ARGS = (
    "--enable omni.flowusd --enable omni.usd.schema.flow "
    "--/rtx/flow/enabled=true --/rtx/flow/pathTracingEnabled=true "
    "--/rtx/flow/rayTracedReflectionsEnabled=true "
    "--/rtx/flow/rayTracedTranslucencyEnabled=true "
    "--/rtx/flow/maxBlocks=32768"
)
# NOTE: composite feature flags such as /rtx/translucency/enabled cannot be
# set here. SimulationContext applies the default "balanced" rendering preset
# after command-line settings, so restore them through RenderCfg instead
# (see configure_city_env_cfg in demo/city/main.py).
FLOW_KIT_ARGS = FLOW_KIT_PRELOAD_ARGS
# 仓库内 usd/effects/（README.md 记录出处与许可）；EAI_NVD_FLOW_ROOT 可指向 NVD 原始下载目录。
_DEFAULT_FLOW_ROOT = Path(__file__).resolve().parents[2] / "usd" / "effects"


def _flow_root_from_env() -> Path:
    configured = os.environ.get("EAI_NVD_FLOW_ROOT")
    return Path(configured).expanduser() if configured else _DEFAULT_FLOW_ROOT


FLOW_ROOT = _flow_root_from_env()
FIRE_USD = FLOW_ROOT / "fire/Fire.usda"
SMOKE_USD = FLOW_ROOT / "smoke/DenseSmoke.usda"

# 仓库布局在前，NVD 原始目录布局作兼容回退。
_PRESET_LAYOUTS = (
    ("fire/Fire.usda", "smoke/DenseSmoke.usda"),
    ("105.1/presets/Fire/Fire.usda", "105.1/presets/Smoke/DenseSmoke.usda"),
    ("105/presets/Fire/Fire.usda", "105/presets/Smoke/DenseSmoke.usda"),
    ("presets/Fire/Fire.usda", "presets/Smoke/DenseSmoke.usda"),
)


def _preset_paths(root: Path) -> tuple[Path, Path]:
    for fire_rel, smoke_rel in _PRESET_LAYOUTS:
        fire, smoke = root / fire_rel, root / smoke_rel
        if fire.is_file() and smoke.is_file():
            return fire, smoke
    fire_rel, smoke_rel = _PRESET_LAYOUTS[0]
    return root / fire_rel, root / smoke_rel


def _author_attr(stage, path, name, type_name, value):
    prim = stage.GetPrimAtPath(path)
    if not prim:
        raise RuntimeError(f"Flow preset did not compose expected prim {path}")
    attr = prim.GetAttribute(name)
    if not attr:
        attr = prim.CreateAttribute(name, type_name, custom=True)
    attr.Set(value)
    return attr


def verify_live_flow(stage, paths):
    """Fail loudly when the owning Kit session did not really load Flow."""
    import carb.settings
    import omni.kit.app
    manager = omni.kit.app.get_app().get_extension_manager()
    for extension in ("omni.flowusd", "omni.usd.schema.flow"):
        if not manager.is_extension_enabled(extension):
            raise RuntimeError(f"{extension} was not loaded at startup; Flow cannot render")
    if not carb.settings.get_settings().get("/rtx/flow/enabled"):
        raise RuntimeError("RTX Flow rendering is disabled")
    disabled = sorted(
        feature for feature in ("translucency", "reflections", "indirectDiffuse")
        if carb.settings.get_settings().get(f"/rtx/{feature}/enabled") is not True
    )
    if disabled:
        raise RuntimeError(
            "Isaac Lab's balanced rendering preset disabled RTX features that "
            f"Flow composites through: {', '.join(disabled)}. Set "
            "env_cfg.sim.render.enable_translucency/reflections/global_illumination "
            "= True before the environment is created."
        )
    for path in paths:
        emitter = stage.GetPrimAtPath(path + "/flowEmitterSphere")
        if not emitter or emitter.GetTypeName() != "FlowEmitterSphere":
            raise RuntimeError(f"Missing live Flow emitter at {path}")
    print(f"[city-demo] Live Flow verified: {', '.join(paths)}", flush=True)


class FlowTimelineClock:
    """Advance Kit time explicitly; EAI still owns every PhysX simulate call.

    Isaac Lab disables playSimulations around render updates. Flow therefore
    needs its timeline advanced independently of the physics accumulator.
    Keep it monotonic across episode replays; forceClear resets the volume.
    """
    def __init__(self, dt):
        import omni.timeline
        from pxr import Usd
        import omni.usd
        self.timeline = omni.timeline.get_timeline_interface()
        stage = omni.usd.get_context().get_stage()
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            self.timeline.set_time_codes_per_second(1.0 / float(dt))
        self.timeline.set_auto_update(False)
        self.time = self.timeline.get_current_time()

    def advance(self, dt):
        import carb.settings
        settings = carb.settings.get_settings()
        previous = settings.get("/app/player/playSimulations")
        self.time += float(dt)
        try:
            settings.set_bool("/app/player/playSimulations", False)
            self.timeline.set_current_time(self.time)
            self.timeline.commit()
        finally:
            settings.set_bool("/app/player/playSimulations", bool(previous))


class FlowPairAdapter:
    """Exactly one Fire and one DenseSmoke; all overrides in session layer."""

    def __init__(self, stage: Any, *, root_path="/World/CityFlow", preset_root=None,
                 fire_path=None, smoke_path=None, density_cell_size_m=0.1):
        self.stage = stage
        self.root_path = root_path.rstrip("/") or "/World/CityFlow"
        self.fire_path = fire_path or self.root_path + "/Fire"
        self.smoke_path = smoke_path or self.root_path + "/DenseSmoke"
        self.paths = (self.fire_path, self.smoke_path)
        self.preset_root = Path(preset_root).expanduser() if preset_root is not None else _flow_root_from_env()
        self.fire_usd, self.smoke_usd = _preset_paths(self.preset_root)
        self.density_cell_size_m = float(density_cell_size_m)
        if self.density_cell_size_m <= 0:
            raise ValueError("density_cell_size_m must be positive")
        self._composed = self._clear_pending = self._closed = False
        self._last_state = None
        self._emitter_attrs = []

    def compose(self):
        from pxr import Gf, Sdf, Usd, UsdGeom
        if self._closed:
            raise RuntimeError("FlowPairAdapter is closed")
        if self._composed:
            return self.paths
        if not self.fire_usd.is_file() or not self.smoke_usd.is_file():
            raise FileNotFoundError(f"Official Flow presets unavailable: {self.fire_usd}, {self.smoke_usd}")
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            UsdGeom.SetStageUpAxis(self.stage, UsdGeom.Tokens.z)
            UsdGeom.SetStageMetersPerUnit(self.stage, 1.0)
            UsdGeom.Xform.Define(self.stage, self.root_path).ClearXformOpOrder()
            for index, (kind, path, asset) in enumerate((
                ("fire", self.fire_path, self.fire_usd),
                ("smoke", self.smoke_path, self.smoke_usd),
            )):
                xform = UsdGeom.Xform.Define(self.stage, path)
                xform.GetPrim().GetReferences().AddReference(asset.resolve().as_posix())
                xform.ClearXformOpOrder()
                emitter = path + "/flowEmitterSphere"
                simulate = path + "/flowSimulate"
                def set_attr(prim_path, name, type_name, value):
                    return _author_attr(self.stage, prim_path, name, type_name, value)
                for layer_path in (emitter, simulate, path + "/flowOffscreen", path + "/flowRender"):
                    set_attr(layer_path, "layer", Sdf.ValueTypeNames.Int, index)
                for name in ("radiusIsWorldSpace", "velocityIsWorldSpace"):
                    set_attr(emitter, name, Sdf.ValueTypeNames.Bool, True)
                attrs = {"kind": kind, "path": emitter, "simulate_path": simulate}
                attrs["position"] = set_attr(emitter, "position", Sdf.ValueTypeNames.Float3, Gf.Vec3f(0))
                attrs["radius"] = set_attr(emitter, "radius", Sdf.ValueTypeNames.Float, .1)
                attrs["velocity"] = set_attr(emitter, "velocity", Sdf.ValueTypeNames.Float3,
                    Gf.Vec3f(0, 0, 4 if kind == "smoke" else 0))
                attrs["enabled"] = set_attr(emitter, "enabled", Sdf.ValueTypeNames.Bool, False)
                set_attr(simulate, "densityCellSize", Sdf.ValueTypeNames.Float, self.density_cell_size_m)
                set_attr(simulate, "maxStepsPerSimulate", Sdf.ValueTypeNames.UInt, 4)
                set_attr(simulate, "forceSimulate", Sdf.ValueTypeNames.Bool, True)
                for name in ("physicsCollisionEnabled", "physicsConvexCollision", "simulateWhenPaused", "forceClear"):
                    set_attr(simulate, name, Sdf.ValueTypeNames.Bool, False)
                set_attr(simulate + "/advection", "gravity", Sdf.ValueTypeNames.Float3, Gf.Vec3f(0, 0, -9.81))
                set_attr(path + "/flowOffscreen/shadow", "lightPosition", Sdf.ValueTypeNames.Float3, Gf.Vec3f(0))
                # Extinction is inverse length: a metre is 100 source cm.
                set_attr(path + "/flowRender/rayMarch", "attenuation", Sdf.ValueTypeNames.Float, 5.0 if kind == "fire" else 100.0)
                set_attr(path + "/flowOffscreen/shadow", "attenuation", Sdf.ValueTypeNames.Float, 4.5 if kind == "fire" else 100.0)
                set_attr(path + "/flowOffscreen/shadow", "lightDirection", Sdf.ValueTypeNames.Float3,
                         Gf.Vec3f(1 if kind == "fire" else -1, -1, 1))
                set_attr(simulate + "/advection/velocity", "fade", Sdf.ValueTypeNames.Float, .01)
                set_attr(simulate + "/summaryAllocate", "speedThreshold", Sdf.ValueTypeNames.Float, .01)
                set_attr(simulate + "/vorticity", "velocityLogScale", Sdf.ValueTypeNames.Float, 100.0)
                self._emitter_attrs.append(attrs)
        self._composed = True
        return self.paths

    def set_ticking(self, enabled):
        """Pulse Flow only around EAI steps, never during UI/capture renders.

        Isaac Lab suppresses Kit playSimulations while rendering; Flow sees
        that callback as paused even with an advancing timeline. These official
        overrides let its solver run without also stepping PhysX.
        """
        from pxr import Sdf, Usd
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            for attrs in self._emitter_attrs:
                _author_attr(self.stage, attrs["simulate_path"], "simulateWhenPaused", Sdf.ValueTypeNames.Bool, bool(enabled))

    def update(self, state: AccidentState):
        from pxr import Gf, Usd
        if not self._composed:
            self.compose()
        if self._closed:
            raise RuntimeError("FlowPairAdapter is closed")
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            if self._clear_pending:
                for attrs in self._emitter_attrs:
                    self.stage.GetPrimAtPath(attrs["simulate_path"]).GetAttribute("forceClear").Set(False)
                self._clear_pending = False
            for attrs, radius in zip(self._emitter_attrs, (state.fire_radius, state.smoke_radius)):
                attrs["position"].Set(Gf.Vec3f(*state.effect_position))
                attrs["radius"].Set(max(0.0, float(radius)))
                attrs["enabled"].Set(bool(state.burning))
        self._last_state = state
        return state

    def reset(self):
        from pxr import Usd
        if not self._composed or self._closed:
            return self.readback_report()
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            for attrs in self._emitter_attrs:
                attrs["enabled"].Set(False)
                self.stage.GetPrimAtPath(attrs["simulate_path"]).GetAttribute("forceClear").Set(True)
        self._clear_pending = True
        self._last_state = None
        return self.readback_report()

    def close(self):
        from pxr import Usd
        if not self._closed:
            self.reset()
            with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
                self.stage.RemovePrim(self.root_path)
            self._closed, self._composed = True, False
        return self.readback_report()

    def readback_report(self):
        emitters = [] if self._closed else [{
            "kind": attrs["kind"], "path": attrs["path"],
            "enabled": bool(attrs["enabled"].Get()),
            "position_m": tuple(float(v) for v in attrs["position"].Get()),
            "radius_m": float(attrs["radius"].Get()),
            "velocity_mps": tuple(float(v) for v in attrs["velocity"].Get()),
        } for attrs in self._emitter_attrs]
        return {
            "composed": self._composed, "closed": self._closed,
            "up_axis": "Z", "meters_per_unit": 1.0,
            "density_cell_size_m": self.density_cell_size_m,
            "paths": list(self.paths), "emitters": emitters,
            "force_clear_requested": self._clear_pending,
            "physical_field_reset": "requested_only",
            "physical_field_reset_limit": "Flow GPU allocations clear on a running Kit/Flow tick; USD readback does not verify voxels.",
            "nano_vdb_readback_enabled": False, "rendered_proof": False,
        }

    readback = readback_report


__all__ = ["CM_TO_M", "FIRE_USD", "SMOKE_USD", "FLOW_ROOT", "FLOW_KIT_ARGS",
           "FLOW_KIT_PRELOAD_ARGS", "FlowPairAdapter", "verify_live_flow"]
