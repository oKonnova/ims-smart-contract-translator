"""
Regression tests. Assertions are about semantics (what must / must not appear).
"""
import json
import os
import re
import pytest

from src.backend.compiler import Compiler
from src.core.validation import InputError, validate_model, validate_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


def compile_model(model, config=None, name="T"):
    c = Compiler(_load(model), specific_config=_load(config) if config else None, contract_name=name)
    return c.compile(), c


def undeclared_agent_prefixes(code, model):
    """No model agent name may leak into the generated code as `agent.attr`."""
    agents = [a["name"] for a in _load(model)["model"]["agents"]]
    declared = set(re.findall(r"public (\w+);", code))          # real state variables may be dereferenced
    return [a for a in agents if a not in declared and re.search(rf"\b{re.escape(a)}\.\w+", code)]


# ---------------------------------------------------------------- auction
def test_auction():
    code, c = compile_model("examples/englishAuctionModel.json", "config/auction_config.json")
    assert "contract T {" in code
    assert "interface IERC721" in code                      # NFT agent type -> interface
    assert "address payable public seller;" in code
    assert re.search(r"function bid\(\) external payable", code)
    assert "nextDay" not in code and "setSender" not in code  # simulation-only actions
    assert "block.timestamp" in code
    assert not undeclared_agent_prefixes(code, "examples/englishAuctionModel.json")


# ---------------------------------------------------------------- hotel
def test_hotel():
    code, c = compile_model("examples/hotelContractModel.json", "config/hotel_config.json")
    assert "interface IERC721" not in code                  # unused interfaces are not emitted
    assert "function book(" in code and "payable" in code   # function_aliases: receive -> book
    assert "Occupy" in code


# ---------------------------------------------------------------- escrow
def test_escrow_structure():
    code, c = compile_model("examples/escrowVulnerable.json")
    # step 1: MSC with message elements is parsed correctly
    assert re.search(r"function pay\(\) external payable", code)
    assert "price == msg.value" in code
    assert "status = DealStatus.AWAITING_DELIVERY" in code
    # step 2: agents resolved
    assert not undeclared_agent_prefixes(code, "examples/escrowVulnerable.json")
    assert "address payable public seller;" in code
    assert "deliveryConfirmed = true;" in code
    # step 3: balance semantics
    assert code.count("address(this).balance") >= 3
    assert "payable(escrowMarketplace)" not in code
    # universality: clock auto-detected, helper protocol dropped, no unused interface
    assert "currentDay" not in code and "block.timestamp / 1 days" in code
    assert "function not" not in code
    assert "IERC721" not in code
    assert "real " not in code


def test_escrow_warnings_are_reported():
    _, c = compile_model("examples/escrowVulnerable.json")
    text = " ".join(c.warnings)
    assert "clock" in text and "'real'" in text


# ---------------------------------------------------------------- config behaviour
def test_explicit_config_overrides_clock_detection():
    cfg = {"mappings": {"agents": {"currentDay": "customDay"}}}
    c = Compiler(_load("examples/escrowVulnerable.json"), specific_config=cfg)
    code = c.compile()
    assert "customDay <= deadline" in code


def test_effectless_actions_can_be_kept():
    cfg = {"naming": {"emit_effectless_actions": True}}
    code = Compiler(_load("examples/escrowVulnerable.json"), specific_config=cfg).compile()
    assert "function not(" in code


def test_group_separator_is_configurable():
    cfg = {"naming": {"group_separator": ""}}      # no grouping: one function per action
    code = Compiler(_load("examples/englishAuctionModel.json"), specific_config=cfg).compile()
    assert "function bid_first(" in code and "function bid_replace(" in code


# ---------------------------------------------------------------- sender-alias detection (no config)
def test_setsender_bound_agent_collapses_to_msg_sender():
    code, c = compile_model("examples/englishAuctionModel.json")   # no domain config
    assert "address payable public bidder" not in code
    assert re.search(r"bids\[msg\.sender\]", code)
    assert re.search(r"payable\(msg\.sender\)\.call", code)


def test_unassigned_role_without_config_warns_instead_of_silently_breaking():
    code, c = compile_model("examples/hotelContractModel.json")    # no domain config
    assert "address public client;" in code
    assert any("client" in w and "address(0)" in w for w in c.warnings)


# ---------------------------------------------------------------- Vyper-style constructor name
def test_alternate_constructor_name_suppresses_trigger_events():
    """
    Regression test for a real bug: EffectClassifier used to compare against the literal
    string "constructor" instead of reading constructor_names from config, so a target
    whose constructor is named e.g. `__init__` (Vyper) would incorrectly emit trigger
    events during construction. Exercised directly at the classifier level so the trigger's
    own value-match guard (which would otherwise mask the bug, see below) can't interfere.
    """
    from src.core.symbols import SymbolTable
    from src.analysis.semantics import SemanticAnalyzer
    from src.frontend.lexer import Lexer
    from src.frontend.parser import Parser

    config = {
        "constructor_names": ["__init__"],
        "triggers": {"status": {"emit": "StatusSet", "args": []}},   # no "value" filter: always fires
        "mappings": {"agents": {}, "types": {}},
    }
    symbols = SymbolTable(config)
    symbols.define("status", "int", is_state=True)
    analyzer = SemanticAnalyzer(config, symbols)
    stmt = Parser(Lexer().tokenize("status = 1")).parse_stmts()[0]

    analyzer.classifier.current_func = "__init__"     # this action IS the constructor
    effects = analyzer.classifier.classify(stmt)
    assert not any(e.type.name == "EVENT_EMIT" for e in effects), \
        "trigger fired during construction: constructor_names lookup is broken"

    analyzer.classifier.current_func = "deposit"       # an ordinary function
    effects = analyzer.classifier.classify(stmt)
    assert any(e.type.name == "EVENT_EMIT" for e in effects), \
        "trigger did not fire outside construction"


def test_unknown_config_key_is_reported():
    c = Compiler(_load("examples/escrowVulnerable.json"), specific_config={"bogus": 1})
    assert any("bogus" in w for w in c.warnings)


# ---------------------------------------------------------------- input validation
def test_config_passed_as_model_gives_actionable_error():
    with pytest.raises(InputError, match="domain CONFIG"):
        validate_model(_load("config/hotel_config.json"), "config/hotel_config.json")


def test_model_passed_as_config_gives_actionable_error():
    with pytest.raises(InputError, match="not a domain config"):
        validate_config(_load("examples/escrowVulnerable.json"), "x.json")
