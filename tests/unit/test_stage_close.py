import logging
from decimal import Decimal

import pytest

from src.app.contracts.conversation import Conversation
from src.app.contracts.sales import SalesStage
from src.app.core.settings.agent import SalesAgentSettings
from src.app.services.dialog.stages.close import CloseStage
from tests.fakes import FakeLeadGateway, StubLLM, UnavailableLeadGateway, compute_quote, control, logged_text


def _closing_conversation() -> Conversation:
    conversation = Conversation(id="c1", stage=SalesStage.CLOSE, service="Deep Tissue Massage", quantity=6)
    conversation.quote = compute_quote("Deep Tissue Massage", Decimal("120.00"), 6)
    conversation.accepted_offer = "upsell"
    conversation.add_user("Dana, dana@example.com")
    return conversation


async def test_close_is_terminal(agent_settings: SalesAgentSettings) -> None:
    stage = CloseStage(
        StubLLM(control("Thanks, your request is in. The front desk will contact you to pick a time.")),
        agent_settings,
        FakeLeadGateway(),
    )
    conversation = Conversation(id="c1", stage=SalesStage.CLOSE)
    conversation.add_user("great, thanks")

    result = await stage.handle(conversation)

    assert result.next_stage is SalesStage.CLOSE


async def test_close_is_told_the_accepted_order(agent_settings: SalesAgentSettings) -> None:
    llm = StubLLM(control("Thank you! Six sessions, 648.00 in total."))
    conversation = Conversation(id="c1", stage=SalesStage.CLOSE, service="Deep Tissue Massage", quantity=6)
    conversation.quote = compute_quote("Deep Tissue Massage", Decimal("120.00"), 6)
    conversation.accepted_offer = "upsell"
    conversation.add_user("yes, six")

    await CloseStage(llm, agent_settings, FakeLeadGateway()).handle(conversation)

    system = llm.calls[-1][0].content
    assert "6 x Deep Tissue Massage" in system
    assert "648.00" in system


async def test_close_does_not_tell_the_model_to_call_the_request_confirmed_or_booked(
    agent_settings: SalesAgentSettings,
) -> None:
    llm = StubLLM(control("Thank you!"))

    await CloseStage(llm, agent_settings, FakeLeadGateway()).handle(_closing_conversation())

    system = llm.calls[-1][0].content
    assert "Do not call it confirmed or booked" in system
    assert "front desk will contact them to pick a time" in system


async def test_the_lead_goes_to_the_front_desk_once_with_the_accepted_order(
    agent_settings: SalesAgentSettings,
) -> None:
    leads = FakeLeadGateway()
    llm = StubLLM(
        [
            control("Thanks, Dana. Your request is in.", name="Dana", contact="dana@example.com"),
            control("You're welcome!", name="Dana", contact="dana@example.com"),
        ]
    )
    stage = CloseStage(llm, agent_settings, leads)
    conversation = _closing_conversation()

    await stage.handle(conversation)
    conversation.add_user("thanks")
    await stage.handle(conversation)  # after the close: nothing more is sent

    (lead,) = leads.leads
    assert (lead.conversation_id, lead.name, lead.contact) == ("c1", "Dana", "dana@example.com")
    assert (lead.service, lead.quantity, lead.total) == ("Deep Tissue Massage", 6, Decimal("648.00"))


async def test_a_lead_without_details_is_still_handed_over(agent_settings: SalesAgentSettings) -> None:
    leads = FakeLeadGateway()
    stage = CloseStage(StubLLM(control("Thanks.", name=None, contact=None)), agent_settings, leads)

    await stage.handle(_closing_conversation())

    (lead,) = leads.leads
    assert lead.name is None
    assert lead.contact is None


async def test_when_ops_core_is_down_the_conversation_still_closes_and_the_log_has_no_personal_data(
    agent_settings: SalesAgentSettings, caplog: pytest.LogCaptureFixture
) -> None:
    leads = UnavailableLeadGateway()
    stage = CloseStage(
        StubLLM(control("Thanks, Dana. Your request is in.", name="Dana Smith", contact="+1 555 0100")),
        agent_settings,
        leads,
    )
    conversation = _closing_conversation()

    with caplog.at_level(logging.INFO):
        result = await stage.handle(conversation)

    assert conversation.closed is True
    assert result.reply.startswith("Thanks, Dana.")
    assert leads.attempts == 1
    failure = [record for record in caplog.records if record.getMessage() == "Lead not saved"]
    assert failure
    logged = logged_text(caplog.records)
    assert "Dana" not in logged
    assert "555 0100" not in logged

    conversation.add_user("anyone there?")
    await stage.handle(conversation)
    assert leads.attempts == 1  # a failed hand-off is not retried on a later turn
