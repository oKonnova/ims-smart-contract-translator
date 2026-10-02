from typing import List, Optional
from dataclasses import dataclass
from enum import Enum

class IRNode: 
    pass

@dataclass
class IRRequire(IRNode): 
    condition: str
    message: str

@dataclass
class IRAssign(IRNode): 
    target: str
    expr: str
    operator: str = "="
    is_state: bool = False
    decl_type: Optional[str] = None
    is_payable_target: bool = False
    # ^ Semantic fact: the target holds an address that may need to be cast to a
    # payable/transferable address type. Whether that requires any syntax at all
    # (Solidity: `payable(...)`; many other languages: nothing) is decided by the
    # Emitter, not here -- IR carries the fact, never the rendered syntax.


class StateMutability(Enum):
    """Semantic classification of a function's interaction with contract state."""
    MUTATING = "mutating"    # has state-changing or environment-interacting effects
    VIEW = "view"            # reads state but does not modify it
    PURE = "pure"            # neither reads nor modifies state

@dataclass
class IRNativeTransfer(IRNode): 
    """
    Abstract representation of the transfer of a native network token.
    The emitter will decide how to implement it: via .call{value} or Coin::transfer.
    """
    recipient: str
    amount: str

@dataclass
class IRExternalCall(IRNode): 
    target: str
    method: str
    args: List[str]

@dataclass
class IREmit(IRNode): 
    event: str
    args: List[str]

@dataclass
class Branch: 
    condition: str
    nodes: List[IRNode]

@dataclass
class FunctionDef:
    name: str
    args: List[str]
    is_payable: bool
    state_mutability: Optional[StateMutability]   # None when not applicable (e.g. is_payable=True, or a constructor)
    common_requires: List[IRRequire]
    branches: List[Branch]
    is_constructor: bool = False