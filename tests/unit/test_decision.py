from src.app.services.dialog.decision import AgentDecision


def test_parses_a_plain_json_object() -> None:
    decision = AgentDecision.parse('{"reply": "hello", "data": {"service": "Massage", "quantity": 3}}')

    assert decision is not None
    assert decision.reply == "hello"
    assert decision.data == {"service": "Massage", "quantity": 3}


def test_parses_json_wrapped_in_a_code_fence() -> None:
    decision = AgentDecision.parse('```json\n{"reply": "hi", "data": {}}\n```')

    assert decision is not None
    assert decision.reply == "hi"


def test_recovers_json_embedded_in_prose() -> None:
    decision = AgentDecision.parse('Sure thing! {"reply": "hi", "data": {}} — hope that helps.')

    assert decision is not None
    assert decision.reply == "hi"


def test_does_not_let_a_brace_inside_a_string_end_the_scan() -> None:
    decision = AgentDecision.parse('{"reply": "use {curly} braces", "data": {}}')

    assert decision is not None
    assert decision.reply == "use {curly} braces"


def test_garbage_is_a_parse_failure() -> None:
    assert AgentDecision.parse("I could not think of anything to say.") is None


def test_missing_reply_is_a_parse_failure() -> None:
    # A control object with no sayable reply is useless to the caller.
    assert AgentDecision.parse('{"data": {"objection": true}}') is None


def test_flag_reads_booleans_and_tolerant_strings() -> None:
    decision = AgentDecision.parse('{"reply": "x", "data": {"a": true, "b": "yes", "c": false, "d": "no"}}')

    assert decision is not None
    assert decision.flag("a") is True
    assert decision.flag("b") is True
    assert decision.flag("c") is False
    assert decision.flag("d") is False
    assert decision.flag("missing") is False
