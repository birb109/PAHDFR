"""Dependency-free entity candidate extraction from archived HTML."""

from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import urlparse


_SKIP_NAMES = {
    "home", "next", "previous", "add to basket", "all products", "catalogue",
    "search", "login", "log in", "register", "menu", "more", "read more",
}


class _CandidateParser(HTMLParser):
    """Extract names from page titles, headings, metadata, and JSON-LD."""

    def __init__(self, original_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.is_books_to_scrape = (urlparse(original_url).hostname or "").casefold() == "books.toscrape.com"
        self.candidates: list[dict[str, Any]] = []
        self._text_stack: list[tuple[str, str, list[str]]] = []
        self._json_ld_parts: list[str] | None = None
        self._seen: set[tuple[str, str]] = set()

    @staticmethod
    def _clean(value: Any) -> str:
        return " ".join(str(value or "").split()).strip()

    def _add(self, entity_type: str, name: Any, confidence: float, **attributes: Any) -> None:
        cleaned_name = self._clean(name)
        if not cleaned_name or len(cleaned_name) > 200 or cleaned_name.casefold() in _SKIP_NAMES:
            return
        key = (entity_type.casefold(), cleaned_name.casefold())
        if key in self._seen:
            return
        self._seen.add(key)
        values: dict[str, Any] = {"name": cleaned_name}
        values.update({k: self._clean(v) for k, v in attributes.items() if self._clean(v)})
        self.candidates.append({
            "entity_type": entity_type,
            "attributes": values,
            "extraction_confidence": confidence,
        })

    def _add_json_ld(self, raw_json: str) -> None:
        try:
            document = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            return

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if not isinstance(value, dict):
                return

            raw_type = value.get("@type")
            if isinstance(raw_type, list):
                raw_type = raw_type[0] if raw_type else None
            if raw_type and value.get("name"):
                type_name = str(raw_type).rsplit("/", 1)[-1].casefold()
                type_map = {
                    "book": "book", "product": "product", "person": "person",
                    "organization": "organization", "localbusiness": "organization",
                    "place": "place", "event": "event", "website": "website",
                    "newsarticle": "article", "article": "article",
                }
                entity_type = type_map.get(type_name, type_name or "entity")
                attrs: dict[str, Any] = {}
                for key in ("url", "isbn", "sku"):
                    item = value.get(key)
                    if isinstance(item, (str, int, float)):
                        attrs[key] = item
                author = value.get("author")
                if isinstance(author, dict):
                    attrs["author"] = author.get("name", "")
                elif isinstance(author, str):
                    attrs["author"] = author
                self._add(entity_type, value.get("name"), 0.98, **attrs)

            for key in ("@graph", "mainEntity", "mainEntityOfPage", "itemListElement"):
                if key in value:
                    visit(value[key])
            # JSON-LD authors, publishers, and nested entities are useful too.
            for key in ("author", "publisher", "brand", "item"):
                if key in value:
                    visit(value[key])

        visit(document)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.casefold(): value for key, value in attrs}
        tag = tag.casefold()

        if tag == "meta":
            key = (attr_map.get("property") or attr_map.get("name") or "").casefold()
            content = attr_map.get("content")
            if content and key in {"og:title", "twitter:title"}:
                self._add("webpage", content, 0.75)
            elif content and key in {"author", "article:author"}:
                self._add("person", content, 0.8)

        if tag == "script" and (attr_map.get("type") or "").casefold() == "application/ld+json":
            self._json_ld_parts = []

        if tag == "title" or tag in {"h1", "h2", "h3"}:
            if tag == "title":
                entity_type, confidence = "webpage", 0.7
            elif self.is_books_to_scrape and tag in {"h1", "h3"}:
                entity_type, confidence = "book", 0.82
            else:
                entity_type, confidence = "heading", 0.65
            self._text_stack.append((tag, entity_type, [str(confidence)]))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "script" and self._json_ld_parts is not None:
            self._add_json_ld("".join(self._json_ld_parts))
            self._json_ld_parts = None
        for index in range(len(self._text_stack) - 1, -1, -1):
            open_tag, entity_type, parts = self._text_stack[index]
            if open_tag == tag:
                self._text_stack.pop(index)
                confidence = float(parts[0])
                self._add(entity_type, " ".join(parts[1:]), confidence)
                break

    def handle_data(self, data: str) -> None:
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)
        for _tag, _entity_type, parts in self._text_stack:
            parts.append(data)


def extract_entity_candidates(html: str, original_url: str) -> list[dict[str, Any]]:
    """Return structured entity candidates from an archived HTML page.

    Extraction uses JSON-LD, social metadata, titles, and headings. It is a
    lightweight candidate extractor, not a statistical named-entity model.
    """
    parser = _CandidateParser(original_url)
    parser.feed(html)
    return parser.candidates
