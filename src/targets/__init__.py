# Import language-specific modules
from .solidity.config import SOLIDITY_STD_CONFIG
from .solidity.emitter import SolidityEmitter

# (In the future there will be imports of Vyper, Rust, etc.)

def get_target_bundle(target_name: str):
    """
    Returns a (Config, EmitterClass) pair for the selected language.
    """
    target_name = target_name.lower()
    
    if target_name == "solidity":
        return SOLIDITY_STD_CONFIG, SolidityEmitter
    
    # elif target_name == "vyper":
    #     return VYPER_CONFIG, VyperEmitter
        
    else:
        raise ValueError(f"Unsupported target language: {target_name}")