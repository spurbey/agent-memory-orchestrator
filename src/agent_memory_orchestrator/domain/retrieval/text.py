from __future__ import annotations

import re


QUERY_STOPWORDS = {
    "about",
    "after",
    "again",
    "and",
    "are",
    "code",
    "did",
    "does",
    "for",
    "from",
    "how",
    "into",
    "made",
    "make",
    "the",
    "this",
    "was",
    "what",
    "when",
    "where",
    "which",
    "why",
    "were",
    "with",
}

HOOK_QUERY_EXPANSION_TERMS = {
    "capture",
    "inject",
    "injection",
    "prompt",
    "userpromptsubmit",
}


def exact_tokens(query: str) -> list[str]:
    tokens = []
    for token in re.findall(r"[A-Za-z0-9_./:-]+", query):
        if len(token) <= 2:
            continue
        if token.lower() in QUERY_STOPWORDS:
            continue
        if "/" in token or "\\" in token or "." in token or "::" in token or re.fullmatch(r"[0-9a-f]{6,40}", token):
            tokens.append(token.replace("\\", "/"))
    return tokens


def normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-zA-Z0-9_./:-]+", str(text).lower()))


def stem_term(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def terms(text: str) -> set[str]:
    out: set[str] = set()
    for token in re.split(r"[^a-zA-Z0-9_]+", text.lower()):
        if len(token) <= 2 or token in QUERY_STOPWORDS:
            continue
        if re.fullmatch(r"[0-9a-f]{16,40}", token):
            continue
        out.add(stem_term(token))
    return out


def expanded_query_terms(query: str) -> set[str]:
    out = terms(query)
    if "hook" in out:
        out.update(HOOK_QUERY_EXPANSION_TERMS)
    return out


def fts_query(query: str) -> str:
    return " OR ".join(sorted(expanded_query_terms(query))[:12])


__all__ = [
    "HOOK_QUERY_EXPANSION_TERMS",
    "QUERY_STOPWORDS",
    "exact_tokens",
    "expanded_query_terms",
    "fts_query",
    "normalize",
    "stem_term",
    "terms",
]
