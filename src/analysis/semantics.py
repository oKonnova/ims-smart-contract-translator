from typing import List
from src.core.ast_nodes import (Stmt, Assignment, MemberAccess, Identifier, CallExpr,
                                BinaryExpr, UnaryExpr, Literal, IndexAccess)
from src.core.effects import Effect
from src.analysis.classifier import EffectClassifier


class SemanticAnalyzer:
    def __init__(self, config, symbol_table):
        self.config = config
        self.symbols = symbol_table
        self.auto_mappings = {}

        # Per-function flags (reset by the compiler for every generated function)
        self.uses_native_value = False   # function receives / references msg.value
        self.reads_state = False         # function reads contract state or the environment

        # Agent resolution (filled in by the compiler)
        self.contract_names = set()      # agent instances that ARE the contract
        self.role_names = set()          # agent instances that are plain accounts (buyer, seller, ...)
        self.sender_aliases = set()      # agents the model declares equal to the current caller

        # Per-action: argument name -> target expression (e.g. {"x": "msg.value"})
        self.bindings = {}
        self.current_args = set()     # argument names of the action being analysed

        self.warnings = []
        self.classifier = EffectClassifier(config, symbol_table, self)

    # ------------------------------------------------------------------ helpers
    def _agents(self):
        return self.config["mappings"]["agents"]

    def is_contract_ref(self, node) -> bool:
        """True if the expression denotes the contract itself."""
        if isinstance(node, Identifier) and node.name in self.contract_names:
            return True
        alias = self._agents().get("contractAddress", "address(this)")
        return self.visit(node) == alias

    def detect_inflow_bindings(self, stmts, arg_names):
        """
        Finds `<contract>.balance = <contract>.balance + <arg>`: the argument is the amount
        of native currency the caller sends, i.e. msg.value in the target language.
        """
        agents = self._agents()
        value_alias = agents.get("value", "msg.value")
        found = {}
        for s in stmts:
            if not (isinstance(s, Assignment) and isinstance(s.target, MemberAccess)
                    and s.target.member == "balance" and self.is_contract_ref(s.target.object)):
                continue
            v = s.value
            if (isinstance(v, BinaryExpr) and v.op == "+" and isinstance(v.right, Identifier)
                    and v.right.name in arg_names):
                if agents.get(v.right.name, v.right.name) != value_alias:
                    found[v.right.name] = value_alias
        return found

    # ------------------------------------------------------------------ visitor
    def visit(self, node):
        agents = self._agents()

        if isinstance(node, Identifier):
            name = node.name
            if name in self.sender_aliases:
                self.reads_state = True
                return agents.get("msg_sender", "msg.sender")
            if name in self.bindings:
                self.uses_native_value = True
                return self.bindings[name]
            if name in self.auto_mappings:
                return self.auto_mappings[name]

            mapped = agents.get(name, name)

            value_alias = agents.get("value")
            if mapped == value_alias or name == "value":
                self.uses_native_value = True

            sym = self.symbols.lookup(mapped)
            if (sym and sym.is_state) or "." in mapped or "(" in mapped:
                self.reads_state = True     # state or environment (block.*, msg.*)
            return mapped

        elif isinstance(node, MemberAccess):
            member = node.member
            obj_node = node.object

            # an agent the MODEL declares equal to the caller (see Compiler._detect_sender_aliases)
            if isinstance(obj_node, Identifier) and obj_node.name in self.sender_aliases:
                self.reads_state = True
                if member == "address":
                    return agents.get("msg_sender", "msg.sender")
                return f"{agents.get('msg_sender', 'msg.sender')}.{agents.get(member, member)}"

            # msg.value / msg.sender written literally in the model
            if isinstance(obj_node, Identifier) and obj_node.name == "msg":
                if member == "value":
                    self.uses_native_value = True
                self.reads_state = True
                return f"msg.{member}"

            # <contract agent>.<attribute>  ->  plain state variable
            if self.is_contract_ref(obj_node):
                contract_alias = agents.get("contractAddress", "address(this)")
                self.reads_state = True
                if member == "balance":
                    return f"{contract_alias}.balance"
                return agents.get(member, member)

            # <role agent>.address  ->  the role's address state variable
            if isinstance(obj_node, Identifier) and obj_node.name in self.role_names and member == "address":
                self.reads_state = True
                return obj_node.name

            obj = self.visit(obj_node)
            mapped_member = agents.get(member, member)
            sender_alias = agents.get("msg_sender", "msg.sender")

            if obj == sender_alias and member == "address":
                return sender_alias
            return f"{obj}.{mapped_member}"

        elif isinstance(node, CallExpr):
            target = self.visit(node.target)
            args = [self.visit(a) for a in node.args]
            sym = self.symbols.lookup(target)

            if (sym and sym.is_mapping) or target in ["bids", "balances"]:
                return f"{target}[{args[0]}]"

            return f"{target}({', '.join(args)})"

        elif isinstance(node, BinaryExpr):
            l = self.visit(node.left)
            r = self.visit(node.right)
            op = node.op

            base_var = l.split('.')[-1].split('[')[0]
            sym = self.symbols.lookup(base_var)

            if sym and sym.type in ["Boolean", "bool"]:
                if op == "==" and r == "0": return f"!{l}"
                if op == "==" and r == "1": return l
                if op == "!=" and r == "0": return l
                if op == "!=" and r == "1": return f"!{l}"

            return f"{l} {op} {r}"

        elif isinstance(node, UnaryExpr):
            inner = self.visit(node.expr)
            if isinstance(node.expr, BinaryExpr):
                inner = f"({inner})"
            return f"{node.op}{inner}"

        elif isinstance(node, Literal):
            return str(node.value)

        elif isinstance(node, IndexAccess):
            return f"{self.visit(node.target)}[{self.visit(node.index)}]"

        return ""

    def analyze_stmt(self, stmt: Stmt) -> List[Effect]:
        return self.classifier.classify(stmt)
