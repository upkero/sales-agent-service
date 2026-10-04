"""Text the customer reads verbatim, one entry per supported language.

This is the deliberate counterpart to `prompts/`, not an overflow of it. A prompt
is an instruction to the model: it lives in `prompts/`, in English, in exactly one
copy, and the reply language is a placeholder inside it. These lines are the
opposite — nobody paraphrases them, the customer receives them character for
character — so they have to exist once per language. Keeping them out of
`prompts/` is what stops the "prompts are English" rule breaking on its first day.

They are what the agent says when the model's own reply is not usable: unparseable
control output, or a reply that states a price no quote contains. So they cannot
come from the model by definition.
"""

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "clarifier": "Sorry, I didn't quite catch that — could you say it once more?",
        "handoff": "Let me take your details and have a specialist follow up with you directly.",
        "price_pending": "I'll check the exact price as soon as I know the service and how many sessions you'd like.",
        "quote_total": "For {quantity} × {service} the total is {total}.",
        "upsell_offer": "There is also a better-value option: {quantity} × {service} for {total} in total. "
        "Would you like that instead?",
        "upsell_total": "That is {quantity} × {service} for {total} in total.",
    },
    "ru": {
        "clarifier": "Извините, я не расслышал. Не могли бы вы повторить?",
        "handoff": "Давайте я передам вас специалисту, который свяжется с вами и всё уточнит.",
        "price_pending": "Я уточню точную цену, как только буду знать услугу и количество сеансов.",
        "quote_total": "{quantity} × {service}: итого {total}.",
        "upsell_offer": "Есть и более выгодный вариант: {quantity} × {service}, итого {total}. "
        "Хотите его вместо текущего?",
        "upsell_total": "Это {quantity} × {service}, итого {total}.",
    },
}

_FALLBACK_LANGUAGE = "en"


def get_message(language: str, key: str) -> str:
    """The line for this language, falling back to English for one we do not carry.

    A missing language is a configuration gap, not a reason to say nothing: the
    prospect gets an English sentence rather than a blank turn. A missing *key*
    is a bug in this repository and raises, because no fallback could be right.
    """
    return _MESSAGES.get(language, _MESSAGES[_FALLBACK_LANGUAGE])[key]
