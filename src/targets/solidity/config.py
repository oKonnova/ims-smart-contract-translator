SOLIDITY_STD_CONFIG = {
    "mappings": {
        "types": {
            "int": "uint256",
            "Boolean": "bool",
            "Bytes": "bytes",
            "Address": "address",
            "function": "mapping" 
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
                "ownership_props": ["owner"]
            },
            "IERC20": {
                "method": "transfer",
                "method_transfer_from": "transferFrom",
                "args_order": ["to", "amount"],
                "ownership_props": ["balance"]
            },
            "nft": { "type": "IERC721" },
            "token": { "type": "IERC20" }
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