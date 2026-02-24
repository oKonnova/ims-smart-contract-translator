from typing import Any
from dataclasses import dataclass
from enum import Enum, auto


# --- Effect System ---
class EffectType(Enum):
    NO_OP = auto()
    STATE_UPDATE = auto()   
    VALUE_INFLOW = auto()   
    VALUE_OUTFLOW = auto()  
    EXTERNAL_CALL = auto()  
    EVENT_EMIT = auto()     

@dataclass
class Effect:
    type: EffectType
    target: Any = None
    payload: Any = None
    operator: str = "="