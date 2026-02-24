from typing import List, Optional
from dataclasses import dataclass

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

@dataclass
class IRNativeTransfer(IRNode): 
    """
    Abstract representation of the transfer of a native network token (ETH, SOL, LUNA, APT).
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
    visibility: str      # For example: "external" or "public"
    modifiers: str       # Any modifiers (e.g., "payable", or an empty string)
    common_requires: List[IRRequire]
    branches: List[Branch]
    is_constructor: bool = False