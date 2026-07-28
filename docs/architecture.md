# Architecture

Layered architecture with strict inward dependency rule: outer layers depend on inner, never the reverse.

```
┌─────────────────────────────────────────────────────┐
│                    api/v1  (HTTP)                   │
├─────────────────────────────────────────────────────┤
│                    services  (business logic)       │
├─────────────────────────────────────────────────────┤
│      repositories        llm  (clients + skills)    │
├─────────────────────────────────────────────────────┤
│                    interfaces  (ABC)                │
├─────────────────────────────────────────────────────┤
│  contracts     models  (ORM)     prompts  (text)    │
├─────────────────────────────────────────────────────┤
│          core / exceptions / bootstrap              │
└─────────────────────────────────────────────────────┘
```

## Layer responsibilities

### `contracts/`
Internal DTOs passed between layers. Use frozen `dataclass` with `slots=True` — lightweight, hashable, no framework dependency.

```python
@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: LLMRole
    content: str
```

Rule: contracts never import from `schemas/`, `api/`, or `services/`.

---

### `schemas/`
Pydantic `BaseModel` classes that live at the HTTP boundary. Used for request parsing and response serialization only. Never passed into services or repositories — convert to contracts at the router level.

```python
class ChatRequest(BaseModel):
    messages: list[MessageSchema]

# In router:
messages = [LLMMessage(role=m.role, content=m.content) for m in body.messages]
response = await service.complete(messages)
return ChatResponse(content=response.content)
```

---

### `models/`
SQLAlchemy ORM models. Represent database tables. Never returned from repositories as-is — map to contracts before crossing the repository boundary.

---

### `interfaces/`
Abstract base classes (ABC) that define the contracts between layers. Services depend on interfaces, not concrete implementations. This enables swapping implementations (real vs stub vs test double) without touching service code.

```python
class LLMClient(ABC):
    @abstractmethod
    async def complete(self, messages: Sequence[LLMMessage]) -> LLMResponse: ...
```

---

### `repositories/`
All database I/O lives here. Repositories receive and return `contracts` objects (never ORM models or schemas). A repository knows about the DB session; services do not.

---

### `llm/`
Everything that knows a language model exists, and nothing else.

| Module | Holds |
|--------|-------|
| `openai_compatible_llm_client.py` | The adapter: SDK in, `LLMResponse` contract out. Provider types stop here. |
| `factory.py` | Wires settings → raw SDK client → our wrapper. The only place `AsyncOpenAI` is instantiated. |
| `prompt_builder.py` | Assembles an ordered `list[LLMMessage]`. Mechanics, no text. |
| `skills/` | One file per skill — see below. |

> **A skill is not a prompt.** A *skill* is a unit of work you ask a model to do
> ("summarise this dialogue", "extract the booking fields", "classify this intent"):
> code, with typed input and a `contracts/` object out. A *prompt* is the text that
> skill sends. They change for different reasons, by different people, at different
> rates — so they live in different places. A `skills.py` holding one string constant
> is a prompt wearing a skill's name.

#### `llm/skills/`

One file per skill, named after the job it does — `summarize_dialogue.py`,
`extract_booking_fields.py`, `answer_from_context.py`:

```python
class SummarizeDialogue:
    """Transcript in, DialogueSummary out. The caller never learns a model was involved."""

    _prompt = prompt("dialogue.summarize")

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    async def run(self, transcript: Sequence[Message], *, reply_language: str) -> DialogueSummary:
        messages = (
            PromptBuilder()
            .system(self._prompt.render(reply_language=reply_language))
            .user(_format_transcript(transcript))
            .build()
        )
        response = await self._client.complete(messages)
        return _parse(response.content)
```

A skill owns four things: which prompt it uses, how the input is laid out, how the
output is parsed, and what contract comes back. It does **not** own the prompt text.

Rule: a skill returns a `contracts/` object, never a raw model string. That is what
lets a service depend on it without knowing a model was involved — and what makes the
failure mode ("the model returned something unparseable") a typed exception raised in
one place instead of a surprise three layers up.

Add an ABC in `interfaces/llm/` only when a service genuinely needs to swap the skill
(a deterministic stub in tests counts). A port with one implementation and no second
caller is ceremony.

---

### `prompts/`
The text, and only the text. One file per prompt, plain Markdown, **written in English**:

```
prompts/
├── __init__.py                 # the loader (~30 lines, stdlib only)
├── dialogue.summarize.md
├── rag.answer.md
└── sales.qualify.md
```

Prompts are the one asset in an LLM service that is *prose*, and treating them as prose
rather than as string constants is what buys:

- **Readable diffs.** Reflowing a paragraph inside a triple-quoted Python constant
  produces a diff nobody can review; the same edit in a `.md` file reads like the prose
  change it is — and reviewing prose is exactly what a prompt review is.
- **Editable by whoever owns the voice.** The prompt is where the product's tone lives.
  It should not require knowing what a raw string literal is.
- **No layer inversion.** `prompts/` imports nothing from the app, so it sits at the
  bottom next to `contracts/`: every layer may read it, it may depend on none. A system
  prompt in `llm/` would make `services/` reach into the adapter layer for a string.

#### Prompts are English; the reply language is a parameter

A prompt is an instruction to a model, and instructions go in English regardless of the
language the conversation is held in. The output language is **one placeholder**, not a
translation of the file:

```markdown
You are {agent_name}, the receptionist taking table reservations for {venue_name}.
You reply only in {reply_language}, whatever language the guest tries.
```

Three reasons, in order of how much they cost to ignore:

1. **One source of truth.** Translating a prompt forks it. The next edit lands in one
   copy, the two drift, and the bug only shows for callers on the other language —
   the hardest kind to notice, because the tests that run are the ones in your language.
2. **Instruction-following is strongest in English.** Every model here is trained
   predominantly on it, and the effect is largest on exactly the small local models this
   portfolio defaults to (`qwen2.5:7b`) — where a wholly non-English system prompt shows
   up first as flakier tool-calling, not as worse prose.
3. **Tokens.** Cyrillic tokenizes at roughly 2-3× the tokens of the equivalent English
   under the usual BPE vocabularies. A translated system prompt pays that on every
   single call, forever, for no capability gained.

The exception is **few-shot examples**: an example is a demonstration of the *output*,
so it goes in the language the output will be in. Instructions English, examples native.

#### Localized verbatim copy is not a prompt

Some strings are not instructions — they are the exact words the user receives, and the
model either speaks them back unchanged or never sees them at all: canned failure
sentences, the retrieval fallback, "let me put you through to a colleague". Those must
exist per language, and they do not belong in `prompts/`:

| | Prompt | Copy |
|---|---|---|
| Read by | the model, as instruction | the user, verbatim |
| Language | English, always | every supported language |
| Shape | paragraphs of prose | one-liners looked up by key |
| Lives in | `prompts/<name>.md` | `messages/` — `dict[lang, dict[key, str]]` |

Keeping them apart is what lets the rule "`prompts/` is English" stay absolute instead
of acquiring an exception on day one. `messages/` sits beside `prompts/` at the bottom
of the diagram: data, keyed, importing nothing.

Placeholders are `str.format` style. The loader extracts them with
`string.Formatter().parse()`, so nothing declares its variables twice and a typo in a
placeholder name is caught at load rather than at render:

```python
from src.app.prompts import prompt

RAG_ANSWER = prompt("rag.answer")          # module scope: missing file = startup crash
RAG_ANSWER.render(reply_language="Russian", context=..., question=...)
RAG_ANSWER.id                              # "rag.answer@8f3ad1c2"
```

Three properties make this production rather than merely tidy:

1. **Eager load.** Every prompt is read, parsed and validated at import. A missing file
   or a malformed placeholder fails at startup, not on the first customer.
2. **Content-addressed id.** `id` is `name@blake2b(text)[:8]`. Log it with every
   completion and a bad answer in yesterday's logs points at the exact prompt bytes
   that produced it. That is also all an A/B comparison needs — no extra machinery.
3. **Shipped with the code.** The files live under `src/`, so the existing
   `COPY src/ src/` already carries them into the image and a prompt is versioned by
   the same commit as the code that reads it. A remote prompt registry buys live edits
   at the price of an image that is no longer self-contained; take that trade when
   someone actually needs to reword a prompt without a deploy, not before.

Rule: **nothing outside `prompts/` contains model-facing prose.** If you are writing a
sentence for a model to read, it belongs in a file here — in English. The halves that
tend to escape this rule and should not: the user-message scaffold
(`"Context:\n…\n\nQuestion: …"`), re-ask instructions, per-stage directives, and persona
fragments assembled from settings. Sentences the *user* receives verbatim go to
`messages/` instead, one entry per language.

---

### `services/`
Business logic. Orchestrates repositories and LLM clients via their interfaces. A service never touches HTTP (no `Request`, no `Response`) and never touches SQL directly.

---

### `api/v1/`
HTTP layer only. Routers validate incoming schemas, call services, and return response schemas. Exception handlers translate app exceptions to JSON. Middleware adds `X-Request-ID` propagation.

```
api/v1/
├── router.py            # top-level APIRouter, includes sub-routers
├── routers/             # one file per feature (health.py, chat.py, ...)
├── dependencies/        # FastAPI Depends helpers (get_llm_client, get_container)
├── middleware/          # request_id middleware
└── exception_handlers.py
```

---

### `core/`
Cross-cutting infrastructure with no business logic.

| Module | Purpose |
|--------|---------|
| `settings/app.py` | `Settings` — root env config |
| `settings/logging.py` | `LoggingSettings` — `LOG_*` prefix |
| `settings/llm.py` | `LLMSettings` — provider, model, temperature, timeouts. Per-skill overrides belong here too: an extraction skill wants temperature 0 and the cheapest model that parses, a customer-facing reply does not |
| `logging.py` | JSON / text formatter setup, request_id filter |
| `request_id.py` | `ContextVar`-based request ID store |

---

### `exceptions/`
Typed exception hierarchy. All app exceptions inherit `BaseAppException` which carries `status_code`, `error_code`, and `detail`. The exception handler in `api/v1/` converts them to a uniform JSON error envelope.

```json
{ "detail": "LLM provider request failed.", "error_code": "llm_generation_error" }
```

---

### `bootstrap/`
`ApplicationContainer` is the manual DI container. Dependencies are `cached_property` fields — instantiated once on first access. The container is stored on `app.state.container` and closed on lifespan shutdown.

```python
class ApplicationContainer:
    @cached_property
    def llm_client(self) -> OpenAICompatibleLLMClient:
        return create_llm_client(LLMSettings())
```

Use `Depends(get_llm_client)` in routers instead of reaching into `app.state` directly.

---

## Adding a new feature

1. Add a `contracts/` DTO if you need a new internal data shape.
2. Add a `schemas/` request/response model for the HTTP surface.
3. Add an `interfaces/` ABC if you have a new external dependency.
4. Implement it in `repositories/` (DB) or `llm/` (LLM).
5. If it asks a model to do something: the text goes in `prompts/`, the code that sends
   it goes in `llm/skills/`, and what comes back is a `contracts/` object.
6. Wire business logic in `services/`.
7. Expose it in `api/v1/routers/` — schema in, contract out, service call in between.
8. Register the new service/repo in `bootstrap/container.py`.
