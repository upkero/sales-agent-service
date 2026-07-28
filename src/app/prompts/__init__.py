"""Prompt registry: Markdown files on disk, loaded once at import.

Model-facing prose lives here as `.md` and nowhere else — not in f-strings inside
services, not as module constants next to the code that sends them. One home means a
prompt can be read, diffed and reviewed as the text it is, and the identifier below
lets a log line say which revision produced a given answer.

Prompts are written in English, and the reply language is the `{reply_language}`
placeholder — never a translated copy of the file. A translation forks the prompt, so
the next edit lands in one copy and not the other; instruction-following is measurably
weaker off English on the small local models this portfolio defaults to; and Cyrillic
tokenizes two to three times more expensively on every single call. Few-shot examples
are the exception: an example demonstrates the *output*, so it is written in the
output language.

Text the user receives verbatim is not a prompt. Error phrases, degradation notices
and fallback lines belong in `messages/` as `dict[lang, dict[key, str]]`, or the
English-only rule breaks on its first day.
"""

from hashlib import blake2b
from pathlib import Path
from string import Formatter

_DIR = Path(__file__).parent


class Prompt:
    """A template plus the identity of its own text.

    `id` is `name@<digest>`. The digest changes the moment the wording does, which
    pins a logged answer to the exact revision that produced it — the thing you need
    on the day yesterday's output cannot be reproduced and nobody remembers editing
    anything.
    """

    __slots__ = ("id", "name", "placeholders", "text")

    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self.text = text
        self.id = f"{name}@{blake2b(text.encode(), digest_size=4).hexdigest()}"
        # Formatter().parse() is how str.format itself reads the template, so this
        # can never disagree with what render() will actually substitute.
        self.placeholders = frozenset(field for _, field, _, _ in Formatter().parse(text) if field)

    def render(self, **values: object) -> str:
        """Fill every placeholder, refusing to guess about a missing one.

        `str.format` raises KeyError naming one field at a time and never says which
        template it came from. Naming all of them, with the prompt id, is the
        difference between a one-line fix and a bisect.
        """
        if missing := self.placeholders - values.keys():
            raise KeyError(f"prompt {self.id} is missing placeholders: {sorted(missing)}")
        return self.text.format(**values)

    def __repr__(self) -> str:
        return f"<Prompt {self.id}>"


def _load() -> dict[str, Prompt]:
    """Read every `.md` beside this module, once, at import time.

    Eager rather than lazy on purpose: a missing or malformed prompt then fails at
    startup, where the whole cost is a container restart, instead of halfway through
    a conversation with a guest on the line.
    """
    return {
        path.stem: Prompt(path.stem, path.read_text(encoding="utf-8").strip())
        for path in sorted(_DIR.glob("*.md"))
    }


_PROMPTS = _load()


def get_prompt(name: str) -> Prompt:
    """Look up a prompt by filename stem (`answer.md` → `"answer"`)."""
    try:
        return _PROMPTS[name]
    except KeyError:
        raise KeyError(f"unknown prompt {name!r}; available: {sorted(_PROMPTS)}") from None
