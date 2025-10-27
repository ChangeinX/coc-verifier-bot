"""Lightweight spaCy helpers for match automation."""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

import spacy

# We rely on the lightweight blank English pipeline so we do not require
# downloading the heavier statistical models at runtime.
_NLP = spacy.blank("en")
_TOKENIZER = _NLP.tokenizer


@lru_cache(maxsize=2048)
def token_forms(value: str) -> tuple[str, ...]:
    """Return normalized token variants for downstream synonym checks.

    The output includes:
    - the raw lower-case token
    - an alphanumeric-only variant (to match `normalize_text` usage)
    - the token lemma when it differs from the surface form
    """

    doc = _TOKENIZER(value)
    forms: set[str] = set()
    for token in doc:
        raw = token.text.lower().strip()
        if not raw:
            continue
        forms.add(raw)

        alnum = "".join(char for char in raw if char.isalnum())
        if alnum:
            forms.add(alnum)

        lemma = token.lemma_.lower().strip()
        if lemma and lemma not in forms:
            forms.add(lemma)

    return tuple(sorted(forms))


def contains_token(tokens: Iterable[str], synonym: str) -> bool:
    lower_syn = synonym.lower()
    normalized_syn = "".join(char for char in lower_syn if char.isalnum())
    for token in tokens:
        if token == lower_syn or token == normalized_syn:
            return True
    return False
