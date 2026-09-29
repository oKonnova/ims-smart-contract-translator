"""
Input validation for models and domain configs.
"""

# Keys a domain config may contain (everything else is reported, not silently ignored)
KNOWN_CONFIG_KEYS = {
    "mappings", "triggers", "events", "payable_funcs", "capabilities", "security_patterns",
    "constructor_names", "contract_agent_id", "context_prefixes",
    "naming", "functions", "function_aliases", "messages", "heuristics",
}


class InputError(Exception):
    """A problem with the user's input files (not a bug in the translator)."""


def validate_model(data, path="<model>"):
    if not isinstance(data, dict):
        raise InputError(f"'{path}': expected a JSON object at top level.")

    if "model" not in data:
        looks_like_config = any(k in data for k in KNOWN_CONFIG_KEYS)
        hint = (" This file looks like a domain CONFIG. Pass the IMS model export as the first argument "
                "and the config with --config.") if looks_like_config else ""
        raise InputError(f"'{path}' is not an IMS model export (no top-level 'model' key).{hint}")

    model = data["model"]
    for key in ("agentTypes", "actions"):
        if key not in model:
            raise InputError(f"'{path}': model has no '{key}' section.")

    for i, act in enumerate(model["actions"]):
        name = act.get("name", f"#{i}")
        elements = act.get("msc", {}).get("elements")
        if not isinstance(elements, list) or not elements:
            raise InputError(f"'{path}': action '{name}' has no MSC elements.")


def validate_config(cfg, path="<config>"):
    """Returns a list of warnings; raises InputError if the file is clearly not a config."""
    if not isinstance(cfg, dict):
        raise InputError(f"'{path}': expected a JSON object at top level.")
    if "model" in cfg and "actions" in cfg.get("model", {}):
        raise InputError(f"'{path}' looks like an IMS model, not a domain config. "
                         f"Pass the model as the first argument; --config expects the domain config.")
    return [f"config '{path}': unknown key '{k}' was ignored" for k in cfg if k not in KNOWN_CONFIG_KEYS]
