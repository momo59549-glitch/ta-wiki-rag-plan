"""Local BM25 retrieval over published knowledge cards using LlamaIndex nodes."""
from __future__ import annotations

import re
from typing import Any

from llama_index.core.schema import TextNode
from llama_index.retrievers.bm25 import BM25Retriever


_CJK_SEQUENCE = re.compile(r"[\u3400-\u9fff]+")
_WORD = re.compile(r"[a-zA-Z0-9_]+")


def _chinese_ngram_tokenizer(text: str) -> list[str]:
    """Tokenize Chinese without requiring a separate dictionary service.

    LlamaIndex BM25's default word pattern treats a complete Chinese sentence as
    one token. Character unigrams, bigrams and trigrams make short pattern names
    retrievable while normal word tokens preserve English/code searches.
    """
    tokens = [item.lower() for item in _WORD.findall(text)]
    for sequence in _CJK_SEQUENCE.findall(text):
        for size in (1, 2, 3):
            tokens.extend(sequence[index : index + size] for index in range(len(sequence) - size + 1))
    return tokens


def _normalized(text: str) -> str:
    return "".join(character.lower() for character in text if character.isalnum())


def _search_terms(text: str) -> str:
    return " ".join(_chinese_ngram_tokenizer(text))


def _title_entities(title: str) -> list[str]:
    entities = []
    for item in re.split(r"[与和、，,（）()]+", title):
        key = _normalized(item)
        if len(key) >= 2:
            entities.append(key)
            if key.endswith("形态") and len(key) > 2:
                entities.append(key[:-2])
    return entities


def search_published_cards(cards: list[dict[str, Any]], query: str, top_k: int = 5) -> list[dict[str, Any]]:
    if not query.strip() or top_k < 1:
        return []
    published = [card for card in cards if card.get("status") == "published"]
    if not published:
        return []
    nodes = []
    for card in published:
        citations = " ".join(str(item.get("citation", "")) for item in card.get("evidence_refs", []))
        text = "\n".join([str(card.get("title", "")), str(card.get("claim", "")), citations, " ".join(card.get("limitations", []))])
        nodes.append(TextNode(id_=card["card_id"], text=_search_terms(text), metadata={"card_id": card["card_id"], "title": card.get("title", ""), "claim": card.get("claim", "")}))
    retriever = BM25Retriever.from_defaults(
        nodes=nodes,
        similarity_top_k=min(max(top_k * 3, top_k), len(nodes)),
        skip_stemming=True,
        token_pattern=r"(?u)\b\w+\b",
    )
    query_key = _normalized(query)
    results = []
    for item in retriever.retrieve(_search_terms(query)):
        title_key = _normalized(str(item.node.metadata["title"]))
        entity_bonus = max((4.0 for entity in _title_entities(str(item.node.metadata["title"])) if entity in query_key), default=0.0)
        title_bonus = (
            8.0
            if query_key == title_key
            else 5.0
            if query_key and title_key.startswith(query_key)
            else 2.0
            if query_key and query_key in title_key
            else 0.0
        ) + entity_bonus
        results.append(
            {
                "card_id": item.node.metadata["card_id"],
                "title": item.node.metadata["title"],
                "claim": item.node.metadata["claim"],
                "score": float(item.score or 0.0) + title_bonus,
            }
        )
    return sorted(results, key=lambda result: (-result["score"], result["card_id"]))[:top_k]
