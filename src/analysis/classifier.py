from typing import List
from src.core.ast_nodes import Stmt, ExprStmt, Assignment, MemberAccess, Identifier, BinaryExpr, Literal
from src.core.effects import Effect, EffectType

class EffectClassifier:
    """
    Effect Analysis Classifier.
    Purely AST-driven. Agnostic to target blockchain language.
    """
    def __init__(self, config, symbol_table, analyzer):
        self.config = config
        self.symbols = symbol_table
        self.analyzer = analyzer
        self.current_func = None

    def classify(self, stmt: Stmt) -> List[Effect]:
        if isinstance(stmt, ExprStmt):
            self.analyzer.visit(stmt.expr) 
            return []

        if not isinstance(stmt, Assignment):
            return []

        target_node = stmt.target
        value_node = stmt.value
        effects = []

        # Generation of abstract string representations for the code generator (IR)
        target_src = self.analyzer.visit(target_node)
        value_src = self.analyzer.visit(value_node)

        contract_alias = self.config["mappings"]["agents"].get("contractAddress")
        sender_alias = self.config["mappings"]["agents"].get("msg_sender")

        # ---------------------------------------------------------
        # 1. VALUE OUTFLOW 
        # ---------------------------------------------------------
        if isinstance(target_node, MemberAccess) and target_node.member == "balance":
            recipient_src = self.analyzer.visit(target_node.object)
            
            if recipient_src == contract_alias: 
                return []

            if isinstance(value_node, BinaryExpr) and value_node.op == "+":
                amount_src = self.analyzer.visit(value_node.right)
                return [Effect(EffectType.VALUE_OUTFLOW, target=recipient_src, payload=amount_src)]
            return []

        # ---------------------------------------------------------
        # 2. EXTERNAL CALLS (Tokens, NFTs)
        # ---------------------------------------------------------
        if isinstance(target_node, MemberAccess):
            prop_name = target_node.member
            obj_node = target_node.object
            obj_name = self.analyzer.visit(obj_node)

            sym = self.symbols.lookup(obj_node.name) if isinstance(obj_node, Identifier) else None

            iface_type = sym.type if sym else None
            if not iface_type and "nft" in obj_name.lower():
                iface_type = self.config["heuristics"]["interfaces"].get("nft", {}).get("type")

            if iface_type and iface_type in self.config["heuristics"]["interfaces"]:
                conf = self.config["heuristics"]["interfaces"][iface_type]
                
                if prop_name in conf.get("ownership_props", []):
                    token_id = f"{obj_name}Id"

                    if value_src == contract_alias:
                        method = conf.get("method_transfer_from", "transferFrom")
                        args = [sender_alias, contract_alias, token_id]
                    else:
                        method = conf.get("method", "safeTransferFrom")
                        args = [contract_alias, value_src, token_id]
                    
                    return [Effect(EffectType.EXTERNAL_CALL, target=obj_name, payload={"method": method, "args": args})]

        # ---------------------------------------------------------
        # 3. STATE UPDATES & EVENTS
        # ---------------------------------------------------------
        op = "="
        if isinstance(value_node, BinaryExpr) and value_node.op == "+":
            if self.analyzer.visit(value_node.left) == target_src:
                op = "+="
                value_src = self.analyzer.visit(value_node.right)

        root_target = target_src.split('[')[0]
        sym = self.symbols.lookup(root_target)

        if sym and sym.type in self.config.get("heuristics", {}).get("interfaces", {}):
            if value_src.startswith("_"): 
                value_src = f"{sym.type}({value_src})"

        # --- AUTO EVENTS ---
        if root_target in self.config.get("triggers", {}) and self.current_func != "constructor":
            trigger = self.config["triggers"][root_target]
            should_emit = True
            if "value" in trigger and value_src.strip() != trigger["value"]: 
                should_emit = False
            if should_emit:
                effects.append(Effect(EffectType.EVENT_EMIT, target=trigger["emit"], payload=trigger["args"]))

        # --- BOOLEAN NORMALIZATION ---
        if sym and sym.type in ["Boolean", "bool"]:
            if value_src == "1": value_src = "true"
            if value_src == "0": value_src = "false"

        effects.append(Effect(EffectType.STATE_UPDATE, target=target_src, payload=value_src, operator=op))

        value_alias = self.config["mappings"]["agents"].get("value", "msg.value")
        if value_alias in value_src:
            self.analyzer.has_eth = True

        return effects