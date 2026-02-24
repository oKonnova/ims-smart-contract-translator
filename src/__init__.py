# src/targets/__init__.py
import importlib

def get_target_bundle(target_name: str):
    """
    Dynamically loads the config and emitter class based on the target language name.
    For example, if target_name == "solidity", it will load src/targets/solidity/
    """
    try:
        # Dynamic module import
        config_module = importlib.import_module(f"src.targets.{target_name}.config")
        emitter_module = importlib.import_module(f"src.targets.{target_name}.emitter")
        
        # We get the config (we expect it to be called {TARGET}_STD_CONFIG)
        config_var_name = f"{target_name.upper()}_STD_CONFIG"
        base_config = getattr(config_module, config_var_name)
        
        # We get the emitter class (we expect it to be called {Target}Emitter)
        class_name = f"{target_name.capitalize()}Emitter"
        emitter_class = getattr(emitter_module, class_name)
        
        return base_config, emitter_class
        
    except ImportError as e:
        raise ValueError(f"Target language '{target_name}' is not supported or required files are missing. Error: {e}")