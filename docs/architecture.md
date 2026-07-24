# Architecture

Layered architecture with strict inward dependency rule: outer layers depend on inner, never the reverse.

```
┌─────────────────────────────────────────────────────┐
│                    api/v1  (HTTP)                   │
├─────────────────────────────────────────────────────┤
│                    services  (business logic)       │
├─────────────────────────────────────────────────────┤
│        repositories          llm  (clients)         │
├─────────────────────────────────────────────────────┤
│                    interfaces  (ABC)                │
├─────────────────────────────────────────────────────┤
│          contracts          models  (ORM)           │
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
Concrete LLM client implementations (`OpenAICompatibleLLMClient`), the factory that wires settings → raw SDK client → our wrapper, and `Skill` definitions (named system-prompt templates).

The factory is the only place where `AsyncOpenAI` is instantiated.

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
| `settings/llm.py` | `LLMSettings` + optional per-skill overrides |
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
5. Wire business logic in `services/`.
6. Expose it in `api/v1/routers/` — schema in, contract out, service call in between.
7. Register the new service/repo in `bootstrap/container.py`.
