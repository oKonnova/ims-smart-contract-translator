import copy
import re
from src.core.symbols import SymbolTable
from src.frontend.lexer import Lexer
from src.frontend.parser import Parser
from src.analysis.semantics import SemanticAnalyzer
from src.core.effects import EffectType
from src.core.ir_nodes import IRRequire, Branch, FunctionDef, IRNativeTransfer, IRExternalCall, IRAssign, IREmit
from src.targets import get_target_bundle
from src.core.validation import validate_config
from src.core.ast_nodes import (BinaryExpr, UnaryExpr, MemberAccess, Identifier, CallExpr,
                                IndexAccess, Assignment)

# Message texts in an MSC that describe a movement of native currency
_VALUE_MESSAGE = re.compile(r"^\s*(pay|funds|transfer|send|refund|deposit|withdraw)\b", re.IGNORECASE)


# Standard EVM execution-context prefixes, shared by Solidity and Vyper alike.
# Single source of truth: every lookup below goes through EVM_CONTEXT_PREFIXES,
# so a --config override changes both call sites identically instead of drifting.
EVM_CONTEXT_PREFIXES = ("msg.", "block.", "tx.")


class Compiler:
    """
    The Orchestrator.
    Manages the compilation pipeline: Load -> Target Selection -> Analysis -> IR Generation -> Code Emission.
    Completely agnostic to the target blockchain language.
    """
    def __init__(self, model_data, specific_config=None, target: str = "solidity",
                 contract_name: str = None, config_path: str = "<config>"):
        """
        :param model_data: Parsed JSON model.
        :param specific_config: Domain-specific configuration .
        :param target: Target language name ("solidity", "vyper"...).
        """
        self.model = model_data['model']
        self.warnings = []
        self.notes = []
        self._msc_cache = {}

        # 1. Load the BASIC language config (Std Lib) from the factory
        base_config, self.EmitterClass = get_target_bundle(target)

        # Deep copy to prevent mutating the global target configuration
        self.config = copy.deepcopy(base_config)

        # 2. Merge with the Domain-specific config safely
        # Generic deep merge of the domain config over the target config
        if specific_config:
            self.warnings.extend(validate_config(specific_config, config_path))
            from src.core.validation import KNOWN_CONFIG_KEYS
            self._deep_merge(self.config, {k: v for k, v in specific_config.items() if k in KNOWN_CONFIG_KEYS})
        if contract_name:
            self.config["contract_name"] = contract_name

        self.symbols = SymbolTable(self.config)
        self.enums = {}
        self.auto_mappings = {}
        self.contract_names = set()
        self.role_names = []          # ordered, model order

        # Agent and global-attribute resolution must happen before the symbol table is built
        self.global_state_vars = []   # (name, ims_type) of globals that become real state variables
        self._resolve_agents()
        self._resolve_globals()
        self.contract_aliases = self._contract_aliases()
        self.sender_aliases = self._detect_sender_aliases()
        self.role_names = [r for r in self.role_names if r not in self.sender_aliases]
        self._init_symbols()
        self.analyzer = SemanticAnalyzer(self.config, self.symbols)
        self.analyzer.auto_mappings = self.auto_mappings
        self.analyzer.contract_names = self.contract_aliases
        self.analyzer.sender_aliases = self.sender_aliases
        self.analyzer.role_names = set(self.role_names)
        self.analyzer.warnings = self.warnings

    # ------------------------------------------------------------------ MSC handling
    def _msc_parts(self, act):
        """
        Splits an MSC into its semantic parts instead of assuming [pre, post].

        elements with 'from'/'to'  -> messages between entities (e.g. Pay(x), Funds(...))
        elements with 'entityId'   -> local annotations of an entity (ignored)
        remaining elements         -> first = precondition, last = postcondition
        """
        if id(act) in self._msc_cache:
            return self._msc_cache[id(act)]
        msc = act['msc']
        entities = {e['id']: e['text'] for e in msc.get('entities', [])}
        elements = msc.get('elements', [])

        messages = [e for e in elements if 'from' in e and 'to' in e]
        plain = [e for e in elements
                 if 'from' not in e and 'to' not in e and 'entityId' not in e and e.get('text', '').strip()]

        pre = plain[0]['text'].strip() if plain else "1"
        if len(plain) > 1:
            post = plain[-1]['text']
        else:
            post = ""
            self.warnings.append(f"{act['name']}: MSC has no postcondition element")
        result = {"pre": pre, "post": post, "messages": messages, "entities": entities}
        self._msc_cache[id(act)] = result
        return result

    def _check_messages(self, act_name, parts, effects):
        """Consistency check: value-carrying messages must be backed by a balance effect."""
        def agent_of(entity_id):
            text = parts["entities"].get(entity_id, "")
            return text.split()[-1] if text.split() else ""

        for m in parts["messages"]:
            if not _VALUE_MESSAGE.match(m.get("text", "")):
                continue
            src, dst = agent_of(m["from"]), agent_of(m["to"])
            if src in self.contract_aliases and dst not in self.contract_aliases:
                if not any(e.type == EffectType.VALUE_OUTFLOW for e in effects):
                    self.warnings.append(
                        f"{act_name}: message '{m['text']}' ({src} -> {dst}) has no matching balance update in the postcondition")
            elif dst in self.contract_aliases and src not in self.contract_aliases:
                if not any(e.type == EffectType.VALUE_INFLOW for e in effects):
                    self.warnings.append(
                        f"{act_name}: message '{m['text']}' ({src} -> {dst}) has no matching balance update in the postcondition")

    # ------------------------------------------------------------------ agents
    def _resolve_agents(self):
        """
        Classifies agent instances:
          * instances of the contract agent type  -> aliases of the contract itself
          * other instances with an 'address' attribute (and no explicit config mapping) -> account roles
        """
        contract_id = self.config.get("contract_agent_id", 1)
        agent_types = self.model.get('agentTypes', [])
        c_type = next((t for t in agent_types if t.get('id') == contract_id), None)
        types_by_name = {t['name']: t for t in agent_types}
        cfg_agents = self.config.get("mappings", {}).get("agents", {})

        for ag in self.model.get('agents', []):
            if c_type and ag.get('type') == c_type['name']:
                self.contract_names.add(ag['name'])
                continue
            if ag['name'] in cfg_agents:
                continue
            t = types_by_name.get(ag.get('type'))
            if t and any(a['name'] == 'address' for a in t.get('attributes', [])):
                self.role_names.append(ag['name'])

    def _note_type(self, where, type_str):
        if type_str in ("real", "float"):
            mapped = self.config.get("mappings", {}).get("types", {}).get(type_str, type_str)
            self.warnings.append(
                f"'{where}' has IMS type '{type_str}' which has no exact target equivalent; mapped to '{mapped}'")

    def _init_symbols(self):
        """
        Phase 1: Building the Symbol Table.
        Registers all types and state variables using purely abstract representations.
        Includes automatic payable detection based on value inflows.
        """
        # --- Automatic Payable Detection (Config-Driven) ---
        payable_agents = set()
        balance_attr = self.config.get("mappings", {}).get("attributes", {}).get("balance", "balance")
        balance_pattern = rf'([a-zA-Z0-9_]+)\.{re.escape(balance_attr)}\s*(?:\+=|=\s*\1\.{re.escape(balance_attr)}\s*\+)'

        all_text = []
        for act in self.model['actions']:
            parts = self._msc_parts(act)
            all_text.append(parts["pre"])
            all_text.append(parts["post"])
            for match in re.finditer(balance_pattern, parts["post"]):
                agent_name = match.group(1)
                if agent_name not in self._contract_aliases():
                    payable_agents.add(agent_name)
        all_text = "\n".join(all_text)

        # --- Type Registration ---
        for t in self.model.get('types', []):
            name = t['name'].strip()
            mapped_name = self.config.get("mappings", {}).get("types", {}).get(name, name)

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
            sys_vars = ["balance"] + list(self.config.get("mappings", {}).get("agents", {}).keys())

            for attr in c_agent['attributes']:
                name = attr['name']
                if name in sys_vars:
                    continue

                type_str = attr['type']

                if type_str == "function" and 'body' in attr:
                    in_args = attr['body'].get('input', [])
                    out_arg = attr['body'].get('output', {})
                    in_type = in_args[0]['name'] if in_args else "Address"
                    out_type = out_arg.get('name', "int")
                    type_str = f"function({in_type})->{out_type}"

                is_payable = False
                if name in payable_agents and type_str in ["Address", "address"]:
                    is_payable = True

                self._note_type(name, type_str)
                self.symbols.define(name, type_str, is_state=True, is_payable=is_payable)

        for g_name, g_type in self.global_state_vars:
            if not self.symbols.lookup(g_name):
                self._note_type(g_name, g_type)
                self.symbols.define(g_name, g_type, is_state=True)

        # --- Account roles (buyer, seller, arbiter, ...) become address state variables ---
        # (sender aliases were already removed from role_names: they map to msg.sender, they don't get one)
        for role in self.role_names:
            if self.symbols.lookup(role):
                continue    # already modelled as a contract attribute
            if not re.search(rf'\b{re.escape(role)}\.(address|{re.escape(balance_attr)})\b', all_text):
                continue    # never referenced
            self.symbols.define(role, "Address", is_state=True, is_payable=(role in payable_agents))
            if not re.search(rf'\b{re.escape(role)}(\.address)?\s*=(?!=)', all_text):
                self.warnings.append(
                    f"'{role}' is declared as a state variable but no action ever assigns it an address; "
                    f"it will always be address(0) unless a constructor sets it or a --config mapping "
                    f"(mappings.agents.{role}) points it elsewhere.")

        # --- External contracts (NFT, token, ...) bound to an interface by their AGENT TYPE ---
        iface_by_type = self.config.get("heuristics", {}).get("interface_by_agent_type", {})
        self.interface_agents = {}
        for ag in self.model.get('agents', []):
            iface = iface_by_type.get(ag.get('type'))
            if iface and re.search(rf'\b{re.escape(ag["name"])}\b', all_text):
                self.interface_agents[ag['name']] = iface
                self.symbols.define(ag['name'], iface, is_state=True)

        # --- Constructor Arguments Registration ---
        # An argument assigned to an interface-bound agent (`nft = _nft`) is that contract's address;
        # every other argument defaults to the abstract integer type.
        constructor_names = self.config.get("constructor_names", ["constructor", "__init__"])
        abstract_int = self.config.get("mappings", {}).get("types", {}).get("int", "int")

        for act in self.model['actions']:
            if act['name'] in constructor_names and 'args' in act:
                assigned_to_iface = set()
                post = self._msc_parts(act)["post"]
                if post.strip():
                    for st in Parser(Lexer().tokenize(post)).parse_stmts():
                        if (isinstance(st, Assignment) and isinstance(st.target, Identifier)
                                and st.target.name in self.interface_agents and isinstance(st.value, Identifier)):
                            assigned_to_iface.add(st.value.name)
                for arg in act['args']:
                    if arg in assigned_to_iface:
                        self.symbols.define(arg, "Address")
                    else:
                        self.symbols.define(arg, abstract_int)

    # ------------------------------------------------------------------ compile
    def compile(self):
        """
        Phase 2: Compiling functions and handing over to the target-specific Emitter.
        """
        naming = self.config.get("naming", {})
        skip_names = set(naming.get("skip_actions", []))

        groups = {}
        for act in self.model['actions']:
            if act['name'] in skip_names:
                continue
            if self._is_simulation_only(act):
                self.notes.append(f"action '{act['name']}' skipped: it only changes environment/simulation attributes")
                continue
            groups.setdefault(self._function_name(act), []).append(act)

        payable_txt = self.config.get("mappings", {}).get("modifiers", {}).get("payable", "payable")
        funcs = []
        for name, acts in groups.items():
            fdef = self._process_func(name, acts)
            no_effects = all(not br.nodes for br in fdef.branches)
            if (no_effects and not fdef.is_constructor and payable_txt not in (fdef.modifiers or "")
                    and not naming.get("emit_effectless_actions", False)):
                self.notes.append(
                    f"actions {[a['name'] for a in acts]} skipped: no state change, transfer or event "
                    f"(guard-only / negative-branch protocol). Set naming.emit_effectless_actions to keep them.")
                continue
            funcs.append(fdef)

        return self.EmitterClass(self.symbols, self.config, self.enums).emit(funcs)

    # ---- naming / grouping (config driven)
    def _function_name(self, act):
        """
        action -> target function. Order: explicit `functions` map, then grouping by a configurable
        separator, then `function_aliases`, then the target's reserved/special function names.
        """
        cfg = self.config
        action_name = act['name']
        explicit = cfg.get("functions", {})
        if action_name in explicit:
            name = explicit[action_name]
        else:
            sep = cfg.get("naming", {}).get("group_separator", "_")
            name = action_name.split(sep)[0] if sep else action_name
        name = cfg.get("function_aliases", {}).get(name, name)

        if name in cfg.get("special_functions", []) and act.get('args'):
            renamed = cfg.get("special_function_rename", "{name}Fn").format(name=name)
            self.warnings.append(
                f"'{name}' is a special function in the target language and cannot take arguments; renamed to '{renamed}' "
                f"(set function_aliases in the domain config to choose another name)")
            name = renamed
        return name

    def _is_env_target(self, node) -> bool:
        """An assignment target that is part of the environment (clock, msg.sender, ...), not contract state."""
        agents = self.config.get("mappings", {}).get("agents", {})
        ctx = tuple(self.config.get("context_prefixes", EVM_CONTEXT_PREFIXES))
        mapped = None
        if isinstance(node, Identifier):
            mapped = agents.get(node.name)
        elif isinstance(node, MemberAccess) and isinstance(node.object, Identifier) \
                and node.object.name in self.contract_aliases:
            mapped = agents.get(node.member)
        return bool(mapped) and any(p in mapped for p in ctx)

    def _is_simulation_only(self, act) -> bool:
        """True if every assignment of the action targets the environment (e.g. nextDay, setSender)."""
        post = self._msc_parts(act)["post"]
        if not post.strip():
            return False
        assigns = [s for s in Parser(Lexer().tokenize(post)).parse_stmts() if isinstance(s, Assignment)]
        return bool(assigns) and all(self._is_env_target(s.target) for s in assigns)

    def _detect_sender_aliases(self) -> set:
        """
        Finds agents the MODEL ITSELF declares equal to the current caller: a postcondition
        that assigns an environment-context attribute (msg.*-mapped) from `<agent>.address`.
        Example: `contract.msg_sender = bidder.address` makes `bidder` an alias of msg.sender.
        This is model-authored intent (the same pattern used to identify simulation-only actions
        like `setSender`).
        """
        aliases = set()
        for act in self.model['actions']:
            post = self._msc_parts(act)["post"]
            if not post.strip():
                continue
            for st in Parser(Lexer().tokenize(post)).parse_stmts():
                if not isinstance(st, Assignment) or not self._is_env_target(st.target):
                    continue
                v = st.value
                if isinstance(v, MemberAccess) and v.member == "address" and isinstance(v.object, Identifier):
                    aliases.add(v.object.name)
        return aliases

    def _contract_aliases(self) -> set:
        """Every name that denotes the contract itself: contract-type agents + config aliases of `address(this)`."""
        agents = self.config.get("mappings", {}).get("agents", {})
        this_alias = agents.get("contractAddress")
        aliases = set(self.contract_names)
        if this_alias:
            aliases |= {k for k, v in agents.items() if v == this_alias and "." not in k}
        return aliases

    @staticmethod
    def _deep_merge(base: dict, extra: dict):
        for k, v in extra.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                Compiler._deep_merge(base[k], v)
            else:
                base[k] = v

    def _resolve_globals(self):
        """
        Global (environment) attributes, e.g. a day counter, need a target-side meaning.
          * mapped in the config                        -> used as configured
          * incremented by some action (x = x + 1)      -> a clock: mapped to the target's time expression
          * anything else that is referenced            -> declared as an ordinary state variable
        The compiler never emits an identifier that has no declaration or mapping.
        """
        agents = self.config.setdefault("mappings", {}).setdefault("agents", {})
        clock_expr = self.config["mappings"].get("clock", "(block.timestamp / 1 days)")

        texts, posts = [], []
        for act in self.model['actions']:
            parts = self._msc_parts(act)
            texts += [parts["pre"], parts["post"]]
            posts.append(parts["post"])
        text = "\n".join(texts)

        for attr in self.model.get('attributes', []):
            n = attr['name']
            if n in agents or not re.search(rf'\b{re.escape(n)}\b', text):
                continue
            is_clock = any(re.search(rf'\b{re.escape(n)}\s*=\s*{re.escape(n)}\s*\+\s*1\b', p) for p in posts)
            if is_clock:
                agents[n] = clock_expr
                self.warnings.append(
                    f"global attribute '{n}' is incremented by an action, so it is treated as a clock and mapped to "
                    f"{clock_expr}. Values compared with it (e.g. deadlines) must use the same unit. "
                    f"Override with mappings.agents.{n} in the domain config.")
            else:
                self.global_state_vars.append((n, attr['type']))
                self.warnings.append(
                    f"global attribute '{n}' has no mapping and is not a clock; it is declared as a contract state variable")

    def _extract_and_conditions(self, node) -> list:
        """
        Recursively flattens top-level '&&' expressions.
        Safely ignores '&&' that are nested inside '||' or parentheses.
        """
        if isinstance(node, BinaryExpr) and node.op == "&&":
            return self._extract_and_conditions(node.left) + self._extract_and_conditions(node.right)
        return [node]

    def _is_funded_balance_guard(self, node, bindings) -> bool:
        """
        `buyer.balance >= x` where x is bound to msg.value: the EVM guarantees the sender can
        pay what it sends, and another account's balance is not the caller's business.
        """
        if not (isinstance(node, BinaryExpr) and bindings):
            return False
        def is_ext_balance(n):
            return (isinstance(n, MemberAccess) and n.member == "balance"
                    and not self.analyzer.is_contract_ref(n.object))
        def is_bound(n):
            return isinstance(n, Identifier) and n.name in bindings
        if node.op in (">=", ">") and is_ext_balance(node.left) and is_bound(node.right):
            return True
        if node.op in ("<=", "<") and is_bound(node.left) and is_ext_balance(node.right):
            return True
        return False

    # ---- lightweight type inference for arguments
    def _walk(self, node):
        yield node
        if isinstance(node, BinaryExpr):
            yield from self._walk(node.left); yield from self._walk(node.right)
        elif isinstance(node, UnaryExpr):
            yield from self._walk(node.expr)
        elif isinstance(node, MemberAccess):
            yield from self._walk(node.object)
        elif isinstance(node, CallExpr):
            yield from self._walk(node.target)
            for a in node.args: yield from self._walk(a)
        elif isinstance(node, IndexAccess):
            yield from self._walk(node.target); yield from self._walk(node.index)
        elif isinstance(node, Assignment):
            yield from self._walk(node.target); yield from self._walk(node.value)

    def _expr_type(self, node):
        if isinstance(node, MemberAccess) and isinstance(node.object, Identifier):
            if node.object.name in self.role_names and node.member == "address":
                return "Address"
            if node.object.name in self.contract_names:
                s = self.symbols.lookup(node.member)
                return s.type if s and not s.is_mapping else None
        if isinstance(node, Identifier):
            s = self.symbols.lookup(node.name)
            return s.type if s and not s.is_mapping else None
        return None

    def _infer_arg_types(self, roots, arg_names):
        inferred = {}
        for root in roots:
            for n in self._walk(root):
                if isinstance(n, BinaryExpr) and n.op in ("==", "!=", "<", ">", "<=", ">="):
                    for a, b in ((n.left, n.right), (n.right, n.left)):
                        if isinstance(a, Identifier) and a.name in arg_names and a.name not in inferred:
                            t = self._expr_type(b)
                            if t: inferred[a.name] = t
                elif isinstance(n, Assignment) and isinstance(n.value, Identifier) and n.value.name in arg_names:
                    if n.value.name not in inferred:
                        t = self._expr_type(n.target)
                        if t: inferred[n.value.name] = t
        return inferred

    def _process_func(self, name, actions):
        """
        Processes a group of actions to create a single function.
        """
        self.analyzer.uses_native_value = False
        self.analyzer.reads_state = False
        self.analyzer.bindings = {}
        self.analyzer.classifier.current_func = name

        local_prefix = self.config.get("naming", {}).get("local_prefix", "temp_")

        # --- 0. Parse every action once: MSC parts, statements, msg.value bindings ---
        parsed = []
        for act in actions:
            parts = self._msc_parts(act)
            stmts = Parser(Lexer().tokenize(parts["post"])).parse_stmts() if parts["post"].strip() else []
            pre_ast = Parser(Lexer().tokenize(parts["pre"])).parse_expr()
            bindings = self.analyzer.detect_inflow_bindings(stmts, act.get('args', []))
            parsed.append({"act": act, "parts": parts, "stmts": stmts, "pre": pre_ast, "bindings": bindings})

        # --- A. Prerequisite Analysis (AST-Driven) ---
        all_guards = []
        bool_type = self.config.get("mappings", {}).get("types", {}).get("Boolean", "bool")
        for p in parsed:
            self.analyzer.bindings = p["bindings"]
            guards = []
            for node in self._extract_and_conditions(p["pre"]):
                if self._is_funded_balance_guard(node, p["bindings"]):
                    continue
                visited_str = self.analyzer.visit(node).strip()
                if visited_str not in ["1", "true", bool_type]:
                    guards.append(visited_str)
            all_guards.append(set(guards))

        common = set.intersection(*all_guards) if all_guards else set()
        guard_msg = self.config.get("messages", {}).get("guard_failed", "Check failed")
        common_reqs = [IRRequire(g, guard_msg) for g in sorted(list(common))]
        branches = []
        args = set()
        func_effects = []

        for i, p in enumerate(parsed):
            act = p["act"]
            self.analyzer.bindings = p["bindings"]
            self.analyzer.current_args = set(act.get('args', []))
            unique = sorted(list(all_guards[i] - common))
            cond_str = " && ".join(unique)

            # --- B. Collection of effects ---
            all_effects = []
            for stmt in p["stmts"]:
                all_effects.extend(self.analyzer.analyze_stmt(stmt))
            self._check_messages(act['name'], p["parts"], all_effects)
            func_effects.extend(all_effects)

            # --- C. Config-driven Security Reordering (CEI) ---
            # Default OFF: the model's own postcondition order is preserved by default. 
            # A model that is deliberately unsafe (e.g. a vulnerable-by-design example) 
            # must stay unsafe in the generated code unless the user opts in here.
            if self.config.get("security_patterns", {}).get("cei", False):
                before = list(all_effects)
                all_effects.sort(key=lambda e: 0 if e.type == EffectType.STATE_UPDATE else (1 if e.type == EffectType.EVENT_EMIT else 2))
                if all_effects != before:
                    self.warnings.append(
                        f"{act['name']}: effects reordered under Checks-Effects-Interactions (security_patterns.cei=true); "
                        f"this no longer matches the order written in the model's postcondition.")

            # --- D. IR generation ---
            ir_nodes = []
            abstract_int = self.config.get("mappings", {}).get("types", {}).get("int", "int")

            for eff in all_effects:
                if eff.type == EffectType.VALUE_OUTFLOW:
                    ir_nodes.append(IRNativeTransfer(eff.target, eff.payload))
                elif eff.type == EffectType.VALUE_INFLOW:
                    pass    # received amount is msg.value; nothing to emit, the function becomes payable
                elif eff.type == EffectType.EXTERNAL_CALL:
                    ir_nodes.append(IRExternalCall(eff.target, eff.payload["method"], eff.payload["args"]))
                elif eff.type == EffectType.STATE_UPDATE:
                    decl = abstract_int if eff.target.startswith(local_prefix) else None
                    target_root = eff.target.split('[')[0]
                    sym = self.symbols.lookup(target_root)
                    is_state = sym.is_state if sym else False

                    is_target_payable = getattr(sym, 'is_payable', False) if sym else False
                    if is_target_payable and "payable(" not in str(eff.payload):
                        cast_fmt = self.config.get("mappings", {}).get("casting", {}).get("payable")
                        if cast_fmt:
                            eff.payload = cast_fmt.format(val=eff.payload)

                    ir_nodes.append(IRAssign(eff.target, eff.payload, eff.operator, is_state, decl))
                elif eff.type == EffectType.EVENT_EMIT:
                    ir_nodes.append(IREmit(eff.target, eff.payload))

            # Argument Collection
            if 'args' in act:
                for a in act['args']:
                    if a in p["bindings"]:
                        continue    # bound to msg.value
                    mapped = self.config.get("mappings", {}).get("agents", {}).get(a, a)
                    ctx_prefixes = tuple(self.config.get("context_prefixes", EVM_CONTEXT_PREFIXES))
                    if mapped.startswith(ctx_prefixes): continue
                    if not a.startswith(local_prefix): args.add(a)

            branches.append(Branch(cond_str, ir_nodes))

        self.analyzer.bindings = {}

        # --- E. Forming abstract typed arguments ---
        inferred = self._infer_arg_types(
            [p["pre"] for p in parsed] + [s for p in parsed for s in p["stmts"]], args)
        typed_args = []
        for arg in sorted(list(args)):
            sym = self.symbols.lookup(arg)
            abstract_t = sym.type if sym else inferred.get(arg, "int")
            self._note_type(f"{name}({arg})", abstract_t)
            mapped_t = self.config.get("mappings", {}).get("types", {}).get(abstract_t, abstract_t)
            typed_args.append(f"{mapped_t} {arg}")

        constructor_names = self.config.get("constructor_names", ["constructor", "__init__"])
        is_constructor = name in constructor_names

        # --- F. Capability-based Modifiers ---
        mut = ""
        is_payable = False

        if self.config.get("capabilities", {}).get("value_transfer", True):
            is_payable = self.analyzer.uses_native_value or name in self.config.get("payable_funcs", [])
            if is_payable:
                mut = self.config.get("mappings", {}).get("modifiers", {}).get("payable", "payable")

        if not is_payable and not is_constructor and self.config.get("capabilities", {}).get("state_mutability", True):
            has_write_effects = any(eff.type in [EffectType.STATE_UPDATE, EffectType.VALUE_OUTFLOW,
                                                 EffectType.EXTERNAL_CALL, EffectType.EVENT_EMIT]
                                    for eff in func_effects)
            if not has_write_effects:
                if self.analyzer.reads_state:
                    mut = self.config.get("mappings", {}).get("modifiers", {}).get("view", "view")
                else:
                    mut = self.config.get("mappings", {}).get("modifiers", {}).get("pure", "pure")

        visibility = ""
        if self.config.get("capabilities", {}).get("visibility_modifiers", True):
            visibility = self.config.get("mappings", {}).get("modifiers", {}).get("external", "external")

        return FunctionDef(name, typed_args, visibility, mut, common_reqs, branches, is_constructor)
