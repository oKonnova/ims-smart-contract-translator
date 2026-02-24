from typing import List
from src.core.ir_nodes import FunctionDef

class BaseEmitter:
    """
    Abstract base class for all target code generators (Solidity, Rust, Move).
    """
    def __init__(self, symbols, config, enums):
        self.symbols = symbols
        self.config = config
        self.enums = enums

    def emit(self, funcs: List[FunctionDef]) -> str:
        raise NotImplementedError("Subclasses must implement emit()")

    def _map_type(self, abstract_type: str) -> str:
        """
        Converts an IMS abstract type to a target language type via config.
        """
        if not abstract_type:
            return ""
        return self.config.get("mappings", {}).get("types", {}).get(abstract_type, abstract_type)