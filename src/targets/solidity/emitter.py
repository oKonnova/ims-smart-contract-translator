from typing import List
from src.targets.base_emitter import BaseEmitter
from src.core.ir_nodes import FunctionDef, IRAssign, IRNativeTransfer, IRExternalCall, IREmit

class SolidityEmitter(BaseEmitter):
    """
    Generates Solidity code.
    Knows all EVM specifics: mapping syntax, require, call{value}, payable.
    """
    def emit(self, funcs: List[FunctionDef]) -> str:
        lines = []
        lines.append("// SPDX-License-Identifier: MIT")
        lines.append("pragma solidity ^0.8.20;\n")

        # 1. Interfaces (from config)
        for iface in self.config.get("interfaces", {}).values():
            lines.extend(iface["source"])
            lines.append("")

        lines.append("contract GeneratedContract {")

        # 2. Enums
        for e_name, values in self.enums.items():
            lines.append(f"    enum {e_name} {{ {', '.join(values)} }}")

        # 3. Events
        for e_name, args in self.config.get("events", {}).items():
            lines.append(f"    event {e_name}({', '.join(args)});")

        # 4. State Variables 
        for name, sym in self.symbols.symbols.items():
            if sym.is_state:
                sol_type = self._resolve_solidity_type(sym)
                lines.append(f"    {sol_type} public {name};")
        lines.append("")

        # 5. Functions
        funcs.sort(key=lambda x: 0 if x.is_constructor else 1)
        for f in funcs:
            lines.extend(self._emit_function(f))

        lines.append("}")
        return "\n".join(lines)

    def _resolve_solidity_type(self, sym) -> str:
        """
        Solidity-specific logic for resolving types.
        Converts an abstract IMS type 'function' to 'mapping(K => V)'.
        """
        if sym.is_mapping:
            k_t = self._map_type(sym.key_type)
            v_t = self._map_type(sym.value_type)
            return f"mapping({k_t} => {v_t})"
        return self._map_type(sym.type)

    def _emit_function(self, f: FunctionDef) -> List[str]:
        lines = []
        
        # Add a space before the modifier (payable), if there is one
        mods = f" {f.modifiers}" if f.modifiers else ""
        
        if f.is_constructor:
            head = f"constructor({', '.join(f.args)}){mods}"
        else:
            head = f"function {f.name}({', '.join(f.args)}) {f.visibility}{mods}"
        
        lines.append(f"    {head} {{")

        for r in f.common_requires:
            lines.append(f'        require({r.condition}, "{r.message}");')

        # Generating branches
        if len(f.branches) == 1:
            lines.extend(self._emit_nodes(f.branches[0].nodes, 2))
        else:
            first = True
            for br in f.branches:
                prefix = "if" if first else "else if"
                
                if not br.condition and not first:
                    lines.append(f"        else {{")
                else:
                    lines.append(f"        {prefix} ({br.condition}) {{")
                
                lines.extend(self._emit_nodes(br.nodes, 3))
                lines.append("        }")
                first = False

        lines.append("    }\n")
        return lines

    def _emit_nodes(self, nodes: List, indent_level: int) -> List[str]:
        lines = []
        ind = "    " * indent_level
        
        for n in nodes:
            if isinstance(n, IRAssign):
                # If it is a local variable, add the type (e.g. 'uint256 temp_amount = ...')
                decl = f"{self._map_type(n.decl_type)} " if n.decl_type else ""
                lines.append(f"{ind}{decl}{n.target} {n.operator} {n.expr};")
                
            elif isinstance(n, IRNativeTransfer):
                lines.append(f'{ind}(bool success, ) = payable({n.recipient}).call{{value: {n.amount}}}("");')
                lines.append(f'{ind}require(success, "Transfer failed");')
                
            elif isinstance(n, IRExternalCall):
                lines.append(f"{ind}{n.target}.{n.method}({', '.join(n.args)});")
                
            elif isinstance(n, IREmit):
                lines.append(f"{ind}emit {n.event}({', '.join(n.args)});")
                
        return lines