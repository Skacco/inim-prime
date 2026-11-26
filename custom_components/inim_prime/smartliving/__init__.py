# Bundled copy of the inim-smartliving library (beta). Edit it there.
"""Python library for INIM SmartLiving panels (SmartLAN board, TCP 5004)."""
from .client import Client, InvalidPinError, UnsupportedModelError
from .commands import ArmMode
from .const import MODELS, Model
from .models import (
    AreaState,
    AreaStatus,
    Configuration,
    Output,
    PanelInfo,
    Scenario,
    Status,
    Zone,
    ZoneState,
    ZoneStatus,
)
from .protocol import (
    SmartLivingChecksumError,
    SmartLivingCommandError,
    SmartLivingConnectionError,
    SmartLivingError,
)

__all__ = [
    "MODELS",
    "ArmMode",
    "InvalidPinError",
    "SmartLivingCommandError",
    "AreaState",
    "AreaStatus",
    "Client",
    "Configuration",
    "Model",
    "Output",
    "PanelInfo",
    "Scenario",
    "SmartLivingChecksumError",
    "SmartLivingConnectionError",
    "SmartLivingError",
    "Status",
    "UnsupportedModelError",
    "Zone",
    "ZoneState",
    "ZoneStatus",
]
