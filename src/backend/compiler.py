import copy
import re
from src.core.symbols import SymbolTable
from src.frontend.lexer import Lexer
from src.frontend.parser import Parser
from src.analysis.semantics import SemanticAnalyzer
from src.core.effects import EffectType
from src.core.ir_nodes import IRRequire, Branch, FunctionDef, IRNativeTransfer, IRExternalCall, IRAssign, IREmit
from src.targets import get_target_bundle
from src.core.ast_nodes import BinaryExpr 

class Compiler:
    """
    The Orchestrator.
    Manages the compilation pipeline: Load -> Target Selection -> Analysis -> IR Generation -> Code Emission.
    Completely agnostic to the target blockchain language.
    """
    def __init__(self, model_data, specific_config=None, target: str = "solidity"):
        """
        :param model_data: Parsed JSON model.
        :param specific_config: Domain-specific configuration (e.g., Auction, Hotel).
        :param target: Target language name ("solidity", "rust", "vyper"...).
        """
        self.model = model_data['model']
        
        # 1. Load the BASIC language config (Std Lib) from the factory
        base_config, self.EmitterClass = get_target_bundle(target)
        
        # Deep copy to prevent mutating the global target configuration
        self.config = copy.deepcopy(base_config)

        # 2. Merge with the Domain-specific config safely
        if specific_config:
            # Safely merge nested mappings
            if "mappings" in specific_config and "agents" in specific_config["mappings"]:
                if "agents" not in self.config.get("mappings", {}):
                    self.config.setdefault("mappings", {})["agents"] = {}
                self.config["mappings"]["agents"].update(specific_config["mappings"]["agents"])
            
            # Merge flat settings
            merge_keys = ["triggers", "events", "payable_funcs", "capabilities", "security_patterns", "constructor_names", "contract_agent_id"]
            for key in merge_keys:
                if key in specific_config:
                    self.config[key] = specific_config[key]

        self.symbols = SymbolTable(self.config)
        self.enums = {}
        self.auto_mappings = {}
        
        # Initialize the symbol table and semantic analyzer
        self._init_symbols()
        self.analyzer = SemanticAnalyzer(self.config, self.symbols)
        self.analyzer.auto_mappings = self.auto_mappings

    def _init_symbols(self):
        """
        Phase 1: Building the Symbol Table.
        Registers all types and state variables using purely abstract representations.
        Includes automatic payable detection based on value inflows.
        """
        # --- Automatic Payable Detection ---
        # Scan all actions to find agents receiving funds.
        # Catches both: "agent.balance +=" AND "agent.balance = agent.balance + ..."
        payable_agents = set()
        for act in self.model['actions']:
            raw_body = act['msc']['elements'][1]['text']
            for match in re.finditer(r'([a-zA-Z0-9_]+)\.balance\s*(?:\+=|=\s*\1\.balance\s*\+)', raw_body):
                agent_name = match.group(1)
                if agent_name not in ["contract", "this"]:
                    payable_agents.add(agent_name)

        # --- Type Registration ---
        for t in self.model.get('types', []):
            name = t['name'].strip()
            mapped_name = self.config.get("mappings", {}).get("types", {}).get(name, name)
            
            # Ignore standard abstract types dynamically
            standard_types = list(self.config.get("mappings", {}).get("types", {}).values())
            if mapped_name in standard_types: 
                continue
                
            if 'values' in t:
                self.enums[mapped_name] = t['values']
                for v in t['values']:
                    self.auto_mappings[v] = f"{mapped_name}.{v}"
                    self.symbols.define(v, mapped_name, is_state=False)
        
        # --- State Variables Registration ---
        contract_id = self.config.get("contract_agent_id", 1)
        c_agent = next((a for a in self.model['agentTypes'] if a.get('id') == contract_id), None)
        
        if c_agent:
            # Collect context variables to ignore dynamically
            sys_vars = ["balance"] + list(self.config.get("mappings", {}).get("agents", {}).keys())
            
            for attr in c_agent['attributes']:
                name = attr['name']
                if name in sys_vars: 
                    continue
                    
                type_str = attr['type']
                
                # Abstract mapping definition (e.g., function(Address)->int)
                if type_str == "function" and 'body' in attr:
                    in_args = attr['body'].get('input', [])
                    out_arg = attr['body'].get('output', {})
                    in_type = in_args[0]['name'] if in_args else "Address"
                    out_type = out_arg.get('name', "int")
                    type_str = f"function({in_type})->{out_type}"

                # Apply Type Refinement for payable addresses
                if name in payable_agents and type_str in ["Address", "address"]:
                    # Assume target maps "address payable" correctly in config if needed
                    type_str = "address payable"

                self.symbols.define(name, type_str, is_state=True)
        
        # --- Constructor Arguments Registration ---
        constructor_names = self.config.get("constructor_names", ["constructor", "init"])
        abstract_int = self.config.get("mappings", {}).get("types", {}).get("int", "int")

        for act in self.model['actions']:
            if act['name'] in constructor_names and 'args' in act:
                for arg in act['args']:
                    if "nft" in arg.lower() and "Id" not in arg:
                        self.symbols.define(arg, "Address")
                        
                        # Dynamically get interface name from heuristics if available
                        if arg == "_nft": 
                            iface = self.config.get("heuristics", {}).get("interfaces", {}).get("nft", {}).get("type", "Interface")
                            self.symbols.define("nft", iface, is_state=True)
                    else: 
                        # Dynamic abstract int type instead of hardcoded types
                        self.symbols.define(arg, abstract_int)

    def compile(self):
        """
        Phase 2: Compiling functions and handing over to the target-specific Emitter.
        """
        groups = {}
        for act in self.model['actions']:
            name = act['name'].split('_')[0]
            if name in ["nextDay", "setSender"]: continue # Skip simulation context
            if name not in groups: groups[name] = []
            groups[name].append(act)
        
        funcs = []
        for name, acts in groups.items():
            func_name = name
            # Special case mapping for fallback receive functions
            if name == "receive" and any('args' in a and a['args'] for a in acts): 
                func_name = "book"
            funcs.append(self._process_func(func_name, acts))
        
        # EMISSION CALL: The Orchestrator delegates string generation completely
        return self.EmitterClass(self.symbols, self.config, self.enums).emit(funcs)

    def _extract_and_conditions(self, node) -> list:
        """
        Recursively flattens top-level '&&' expressions (Conjunctive Normal Form extraction).
        Safely ignores '&&' that are nested inside '||' or parentheses.
        """
        if isinstance(node, BinaryExpr) and node.op == "&&":
            return self._extract_and_conditions(node.left) + self._extract_and_conditions(node.right)
        return [node]

    def _process_func(self, name, actions):
        """
        Processes a group of actions to create a single function.
        Implements normalization of conditions and abstract IR generation.
        """
        # Reset the environment state tracker for the current function
        self.analyzer.has_eth = False
        self.analyzer.classifier.current_func = name

        # --- A. Prerequisite Analysis (AST-Driven) ---
        all_guards = []
        for act in actions:
            raw = act['msc']['elements'][0]['text'].strip()
            
            # Parse the ENTIRE string into a single AST
            root_expr = Parser(Lexer().tokenize(raw)).parse_expr()
            
            # Extract top-level conditions safely via AST traversal
            extracted_nodes = self._extract_and_conditions(root_expr)
            
            guards = []
            bool_type = self.config.get("mappings", {}).get("types", {}).get("Boolean", "bool")
            
            for node in extracted_nodes:
                visited_str = self.analyzer.visit(node).strip()
                # Ignore trivial conditions
                if visited_str not in ["1", "true", bool_type]:
                    guards.append(visited_str)
                    
            all_guards.append(set(guards))
        
        # Identify common requirements
        common = set.intersection(*all_guards) if all_guards else set()
        common_reqs = [IRRequire(g, "Check failed") for g in sorted(list(common))]
        branches = []
        args = set()

        for i, act in enumerate(actions):
            unique = sorted(list(all_guards[i] - common))
            cond_str = " && ".join(unique)
            
            raw_body = act['msc']['elements'][1]['text']
            stmts = Parser(Lexer().tokenize(raw_body)).parse_stmts()

            # --- B. Collection of effects ---
            all_effects = []
            for stmt in stmts:
                all_effects.extend(self.analyzer.analyze_stmt(stmt))

            # --- C. Config-driven Security Reordering (CEI) ---
            # Reorder ONLY if the target language explicitly requires it (e.g., Solidity)
            if self.config.get("security_patterns", {}).get("cei", True):
                all_effects.sort(key=lambda e: 0 if e.type == EffectType.STATE_UPDATE else (1 if e.type == EffectType.EVENT_EMIT else 2))

            # --- D. IR generation ---
            ir_nodes = []
            abstract_int = self.config.get("mappings", {}).get("types", {}).get("int", "int")

            for eff in all_effects:
                if eff.type == EffectType.VALUE_OUTFLOW:
                    ir_nodes.append(IRNativeTransfer(eff.target, eff.payload))
                elif eff.type == EffectType.EXTERNAL_CALL:
                    ir_nodes.append(IRExternalCall(eff.target, eff.payload["method"], eff.payload["args"]))
                elif eff.type == EffectType.STATE_UPDATE:
                    decl = abstract_int if eff.target.startswith("temp_") else None
                    target_root = eff.target.split('[')[0]
                    sym = self.symbols.lookup(target_root)
                    is_state = sym.is_state if sym else False
                    
                    # --- Automatic Target-Agnostic Type Casting ---
                    # e.g., casting address to address payable in Solidity
                    if sym and "payable" in sym.type and "payable(" not in str(eff.payload):
                        # Use casting format from config, fallback to Solidity style
                        cast_fmt = self.config.get("mappings", {}).get("casting", {}).get("payable", "payable({val})")
                        eff.payload = cast_fmt.format(val=eff.payload)
                    
                    ir_nodes.append(IRAssign(eff.target, eff.payload, eff.operator, is_state, decl))
                elif eff.type == EffectType.EVENT_EMIT:
                    ir_nodes.append(IREmit(eff.target, eff.payload))
            
            # Argument Collection
            if 'args' in act:
                for a in act['args']:
                    mapped = self.config.get("mappings", {}).get("agents", {}).get(a, a)
                    # Ignore context variables dynamically
                    if mapped.startswith(("msg.", "env.", "info.")): continue
                    if not a.startswith("temp_"): args.add(a)
            
            branches.append(Branch(cond_str, ir_nodes))

        # --- E. Forming abstract typed arguments ---
        typed_args = []
        for arg in sorted(list(args)):
            sym = self.symbols.lookup(arg)
            abstract_t = sym.type if sym else "int"
            
            # Map abstract type to the target language type via config
            mapped_t = self.config.get("mappings", {}).get("types", {}).get(abstract_t, abstract_t)
            typed_args.append(f"{mapped_t} {arg}")

        # --- F. Capability-based Modifiers ---
        mut = ""
        # Check if target supports native value transfer (payable)
        if self.config.get("capabilities", {}).get("value_transfer", True):
            is_payable = self.analyzer.has_eth or name in self.config.get("payable_funcs", [])
            if is_payable:
                mut = self.config.get("mappings", {}).get("modifiers", {}).get("payable", "payable")

        visibility = ""
        # Check if target uses explicit visibility modifiers (external/public)
        if self.config.get("capabilities", {}).get("visibility_modifiers", True):
            visibility = self.config.get("mappings", {}).get("modifiers", {}).get("external", "external")
        
        # Target-agnostic constructor identification
        constructor_names = self.config.get("constructor_names", ["constructor", "init"])
        is_constructor = name in constructor_names
        
        return FunctionDef(name, typed_args, visibility, mut, common_reqs, branches, is_constructor)