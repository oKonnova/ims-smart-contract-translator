from typing import List
from src.core.ast_nodes import Stmt, MemberAccess, Identifier, CallExpr, BinaryExpr, Literal, IndexAccess
from src.core.effects import Effect
from src.analysis.classifier import EffectClassifier

class SemanticAnalyzer:
    def __init__(self, config, symbol_table):
        self.config = config
        self.symbols = symbol_table
        self.auto_mappings = {}
        self.has_eth = False
        self.classifier = EffectClassifier(config, symbol_table, self)

    def visit(self, node):
        if isinstance(node, Identifier):
            name = node.name
            if name in self.auto_mappings: 
                return self.auto_mappings[name]
            
            mapped = self.config["mappings"]["agents"].get(name, name)
            
            # We track the use of funds (native currency)
            value_alias = self.config["mappings"]["agents"].get("value")
            if mapped == value_alias or name == "value": 
                self.has_eth = True
            
            return mapped

        elif isinstance(node, MemberAccess):
            obj = self.visit(node.object)
            member = node.member
            
            mapped_member = self.config["mappings"]["agents"].get(member, member)
            contract_alias = self.config["mappings"]["agents"].get("contractAddress", "address(this)")
            sender_alias = self.config["mappings"]["agents"].get("msg_sender", "msg.sender")
            
            if obj == sender_alias and member == "address":
                return sender_alias
            
            # Strip the contract prefix for normal state variables
            if obj in [contract_alias, "contract", "this", "GeneratedContract"]:
                if member == "balance": 
                    return f"{contract_alias}.balance"
                return mapped_member
            
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

        elif isinstance(node, Literal): 
            return str(node.value)
            
        elif isinstance(node, IndexAccess): 
            return f"{self.visit(node.target)}[{self.visit(node.index)}]"
            
        return ""

    def analyze_stmt(self, stmt: Stmt) -> List[Effect]:
        return self.classifier.classify(stmt)