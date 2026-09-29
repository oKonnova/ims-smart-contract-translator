SOLIDITY_STD_CONFIG = {
    # Names the target language reserves for special functions (they cannot take arguments)
    "special_functions": ["receive", "fallback"],
    "special_function_rename": "{name}Fn",
    # Identifiers that denote the execution context, not contract state
    "context_prefixes": ["msg.", "block.", "tx."],
    "messages": {"guard_failed": "Check failed", "transfer_failed": "Transfer failed"},
    "naming": {"group_separator": "_", "local_prefix": "temp_", "emit_effectless_actions": False},
    "mappings": {
        "types": {
            "int": "uint256",
            "Boolean": "bool",
            "Bytes": "bytes",
            "Address": "address",
            "function": "mapping",
            "real": "uint256"
        },
        "casting": {
            "payable": "payable({val})"
        },
        "agents": {
            "msg_sender": "msg.sender",
            "contractAddress": "address(this)",
            "this": "address(this)",
            "contract": "address(this)",
            "timestamp": "block.timestamp",
            "seconds": "1 seconds",
            "minutes": "1 minutes",
            "hours": "1 hours",
            "days": "1 days",
            "weeks": "1 weeks",
            "address_0": "address(0)",
            "value": "msg.value"
        },
        "clock": "(block.timestamp / 1 days)",
        "modifiers": {
            "payable": "payable",
            "external": "external",
            "public": "public"
        }
    },

    "heuristics": {
        "interfaces": {
            "IERC721": {
                "method": "safeTransferFrom",
                "method_transfer_from": "transferFrom",
                "args_order": ["from", "to", "tokenId"],
                "ownership_props": ["owner"],
                "token_id": "{agent}Id"
            },
            "IERC20": {
                "method": "transfer",
                "method_transfer_from": "transferFrom",
                "args_order": ["to", "amount"],
                "ownership_props": ["balance"]
            },
        },
        "interface_by_agent_type": {
            "NFT": "IERC721",
            "Token": "IERC20"
        }
    },

    "interfaces": {
        "IERC721": {
             "source": [
                "interface IERC721 {",
                "    function safeTransferFrom(address from, address to, uint256 tokenId) external;",
                "    function transferFrom(address from, address to, uint256 tokenId) external;",
                "}"
            ]
        }
    }
}