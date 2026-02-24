import re
from dataclasses import dataclass
from typing import Optional

@dataclass
class Symbol:
    """
    An abstract representation of a system variable or entity.
    Stores native IMS types (e.g., 'int', 'function' etc).
    """
    name: str
    type: str  # Native IMS type
    is_state: bool
    is_mapping: bool = False
    key_type: Optional[str] = None
    value_type: Optional[str] = None

class SymbolTable:
    """
    Compiler semantic store.
    Only works with types supported by IMS.
    """
    def __init__(self, config):
        self.symbols = {}
        self.config = config

    def define(self, name: str, type_str: str, is_state: bool = False):
        """
        Registers a new symbol.
        If the type is IMS function(K)->V, we store it as 'function'.
        """
        # Recognize the IMS functional type: function(Key) -> Value
        func_match = re.search(r"function\s*\((.*?)\)\s*->\s*(.*)", type_str, re.IGNORECASE)
        
        is_mapping = False
        k_t, v_t = None, None
        final_abstract_type = type_str

        if func_match:
            raw_k = func_match.group(1).strip()
            raw_v = func_match.group(2).strip()
            
            k_t = raw_k
            v_t = raw_v
            
            final_abstract_type = "function"
            is_mapping = True

        self.symbols[name] = Symbol(
            name=name, 
            type=final_abstract_type, 
            is_state=is_state, 
            is_mapping=is_mapping, 
            key_type=k_t, 
            value_type=v_t
        )

    def lookup(self, name: str) -> Optional[Symbol]:
        return self.symbols.get(name)