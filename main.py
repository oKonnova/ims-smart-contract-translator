import json
import argparse
from src.backend.compiler import Compiler

def main():
    parser = argparse.ArgumentParser(description="IMS Model-Driven Compiler")
    parser.add_argument("input_file", help="Path to IMS JSON model (e.g., export.json)")
    parser.add_argument("--out", help="Output file path (e.g., output.sol)", required=True)
    parser.add_argument("--target", help="Target language (solidity, rust, etc.)", default="solidity")
    parser.add_argument("--config", help="Path to specific domain config JSON (optional)", default=None)
    
    args = parser.parse_args()

    with open(args.input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    specific_config = None
    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            specific_config = json.load(f)
            print(f"Loaded domain specific config from {args.config}")
    
    print(f"Compiling {args.input_file} to {args.target}...")
    
    compiler = Compiler(data, specific_config=specific_config, target=args.target)
    
    code = compiler.compile()
    
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(code)
        print(f"Done! Smart contract successfully saved to {args.out}")

if __name__ == "__main__":
    main()