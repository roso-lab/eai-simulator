"""Isaac Sim Env DIY authoring extension."""

from .model import AuthoringModel, AuthoringRobot

__all__ = ["AuthoringModel", "AuthoringRobot"]

try:
    from .extension import EnvDiyExtension
except ModuleNotFoundError as exc:
    if not (exc.name == "omni" or str(exc.name).startswith("omni.")):
        raise
else:
    __all__.append("EnvDiyExtension")
