import json
import os
import re
import sys
import argparse

from src.backend.compiler import Compiler
from src.core.validation import InputError, validate_model, validate_config


def _load_json(path, what):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise InputError(f"{what} file not found: {path}")
    except json.JSONDecodeError as e:
        raise InputError(f"{what} file '{path}' is not valid JSON: {e}")


def _default_contract_name(model_path):
    """CamelCase identifier derived from the model file name."""
    stem = os.path.splitext(os.path.basename(model_path))[0]
    parts = re.split(r'[^0-9a-zA-Z]+', stem)
    name = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if not name or name[0].isdigit():
        name = "Contract" + name
    return name


def main():
    parser = argparse.ArgumentParser(
        description="IMS Model-Driven Compiler",
        epilog="Example: python main.py examples/hotelContractModel.json "
               "--config config/hotel_config.json --out out/HotelRoom.sol")
    parser.add_argument("input_file", help="IMS model export (JSON with a top-level 'model' key)")
    parser.add_argument("--out", help="Output file path (e.g., out/Contract.sol)", required=True)
    parser.add_argument("--target", help="Target language (solidity, ...)", default="solidity")
    parser.add_argument("--config", help="Domain config JSON (optional, NOT the model)", default=None)
    parser.add_argument("--name", help="Generated contract name (default: derived from the model file name)", default=None)
    args = parser.parse_args()

    try:
        data = _load_json(args.input_file, "Model")
        validate_model(data, args.input_file)

        specific_config = None
        if args.config:
            specific_config = _load_json(args.config, "Config")
            print(f"Loaded domain specific config from {args.config}")

        contract_name = args.name or _default_contract_name(args.input_file)
        print(f"Compiling {args.input_file} to {args.target} as '{contract_name}'...")

        compiler = Compiler(data, specific_config=specific_config, target=args.target,
                            contract_name=contract_name, config_path=args.config)
        code = compiler.compile()
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except ValueError as e:      # e.g. unsupported target
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    for n in compiler.notes:
        print(f"NOTE: {n}")
    for w in dict.fromkeys(compiler.warnings):     # de-duplicate, keep order
        print(f"WARNING: {w}")

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(code)
    print(f"Done! Smart contract successfully saved to {args.out}")


if __name__ == "__main__":
    main()
