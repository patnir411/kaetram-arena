"""Round-trip tests for scripts/opd/canonicalize.py against verbatim r2 specimens."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "opd"))

from canonicalize import canonicalize_text, is_malformed, recover_tool_calls  # noqa: E402


def test_recover_call_syntax_string():
    r = recover_tool_calls('<function=query_quest("Foresting")>\n</function>')
    assert r == [{"name": "query_quest", "args": {"quest_name": "Foresting"}}]


def test_recover_call_syntax_kwarg_and_int():
    r = recover_tool_calls('<function=craft_item("cooking", "cookedshrimp", 5)>')
    assert r == [{"name": "craft_item",
                  "args": {"skill": "cooking", "recipe_key": "cookedshrimp", "count": 5}}]


def test_recover_kwarg_in_key_bool():
    r = recover_tool_calls(
        "<function=interact_npc>\n<parameter=npc_name>\nHerby\n</parameter>\n"
        "<parameter=accept_quest_offer=True>\nTrue\n</parameter>\n</function>")
    assert r == [{"name": "interact_npc",
                  "args": {"npc_name": "Herby", "accept_quest_offer": True}}]


def test_recover_nothing_in_prose():
    assert recover_tool_calls("just reasoning about (220, 107)") == []


def test_recover_unknown_tool_skipped():
    assert recover_tool_calls('<function=teleport("home")>') == []


def test_kwarg_in_key_with_duplicate_body():
    # agent_0 r2 session_126 verbatim shape
    s = ("<tool_call>\n<function=interact_npc>\n"
         "<parameter=npc_name>\nHerby Mc. Herb\n</parameter>\n"
         "<parameter=accept_quest_offer=True>\nTrue\n</parameter>\n"
         "</function>\n</tool_call>")
    out, n = canonicalize_text(s)
    assert n == 1
    assert "<parameter=accept_quest_offer>\nTrue\n</parameter>" in out
    assert "=True>" not in out
    assert not is_malformed(out)


def test_kwarg_in_key_bare_no_body():
    s = "<parameter=accept_quest_offer=True>"
    out, n = canonicalize_text(s)
    assert n == 1
    assert out == "<parameter=accept_quest_offer>\nTrue\n</parameter>"


def test_call_syntax_positional_string():
    # 103x in r2 agent_1
    s = ('<tool_call>\n<function=query_quest("Herbalist\'s Desperation")>\n'
         "</function>\n</tool_call>")
    out, n = canonicalize_text(s)
    assert n == 1
    assert "<function=query_quest>" in out
    assert "<parameter=quest_name>\nHerbalist's Desperation\n</parameter>" in out


def test_call_syntax_kwarg():
    # 63x form: <function=gather(resource_name="Blueberry Bush")>
    s = '<tool_call>\n<function=gather(resource_name="Blueberry Bush")>\n</function>\n</tool_call>'
    out, n = canonicalize_text(s)
    assert n == 1
    assert "<parameter=resource_name>\nBlueberry Bush\n</parameter>" in out


def test_call_syntax_multi_positional():
    s = '<function=craft_item("cooking", "cookedshrimp", 5)>\n</function>'
    out, n = canonicalize_text(s)
    assert n == 1
    assert "<parameter=skill>\ncooking\n</parameter>" in out
    assert "<parameter=recipe_key>\ncookedshrimp\n</parameter>" in out
    assert "<parameter=count>\n5\n</parameter>" in out


def test_corrupted_close_tag():
    s = ("<function=eat_food>\n<parameter=slot>\n3\n</number>\n"
         "</function>")
    out, n = canonicalize_text(s)
    assert n == 1
    assert "<parameter=slot>\n3\n</parameter>" in out
    assert "</number>" not in out


def test_idempotent_on_canonical():
    s = ("<tool_call>\n<function=gather>\n"
         "<parameter=resource_name>\nOak\n</parameter>\n"
         "</function>\n</tool_call>")
    out, n = canonicalize_text(s)
    assert n == 0
    assert out == s


def test_prose_with_parens_untouched():
    s = "I should gather (maybe Oak?) and then navigate to (220, 107)."
    out, n = canonicalize_text(s)
    assert n == 0 and out == s


def test_unknown_tool_falls_back_none():
    s = '<function=teleport("home")>\n</function>'
    assert canonicalize_text(s) is None


def test_unterminated_quote_falls_back_none():
    s = '<function=gather("Oak)>\n</function>'
    assert canonicalize_text(s) is None


def test_dangling_think_untouched():
    # the dominant r2 dialect must not be altered
    s = "Let me observe first.\n</think>\n\n<tool_call>\n<function=observe>\n</function>\n</tool_call>"
    out, n = canonicalize_text(s)
    assert n == 0 and out == s
