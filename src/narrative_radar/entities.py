"""Entity lexicon + resolution.

Structured signals (HLTV player/team hrefs) resolve by id and are always
unambiguous. Text resolution is dictionary-based with an ambiguity gate:
gamer tags that collide with common English words (magic, device, rain,
hope, forever, hate, ...) only match case-sensitively, exactly as written
in the lexicon; unambiguous aliases match case-insensitively on word
boundaries. General-purpose NER is deliberately NOT used — it is the wrong
tool for tags like "s1mple" or "device" (see README, design decision 3).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Entity:
    entity_id: str
    kind: str                      # 'team' | 'player'
    canonical: str
    aliases: tuple[str, ...]
    ambiguous: bool = False
    hltv_id: int | None = None


@dataclass(frozen=True)
class TextMention:
    entity_id: str
    alias: str
    layer: str                     # 'text' | 'link' | 'flair-link'


class EntityLexicon:
    """CSV columns: entity_id,kind,canonical,aliases,ambiguous,hltv_id
    (aliases pipe-separated; canonical is always also an alias)."""

    def __init__(self, entities: list[Entity]):
        self.entities = {e.entity_id: e for e in entities}
        self._by_hltv: dict[tuple[str, int], Entity] = {
            (e.kind, e.hltv_id): e for e in entities if e.hltv_id is not None}
        ci_parts, cs_parts = [], []
        self._alias_owner: dict[str, Entity] = {}
        for e in entities:
            for a in {e.canonical, *e.aliases}:
                a = a.strip()
                if not a:
                    continue
                if e.ambiguous and (a == a.lower() or a == a.upper()):
                    # The gate for ambiguous aliases is case-sensitivity,
                    # which is meaningless at BOTH case extremes: 'hate' /
                    # 'device' can never be gated, and ALL-CAPS names like
                    # 'JUST' or 'BUT' collide with emphasis-caps in comments
                    # (measured on 748k real posts). Neither text-matches;
                    # the id-resolved link layer carries them (decision 3).
                    # Only MixedCase ambiguous aliases (NiKo, Spirit, NiP)
                    # are case-gateable.
                    continue
                key = a if e.ambiguous else a.lower()
                self._alias_owner[key] = e
                pat = re.escape(a)
                (cs_parts if e.ambiguous else ci_parts).append(pat)
        # longest-first so "Team Spirit" wins over "Spirit"
        ci_parts.sort(key=len, reverse=True)
        cs_parts.sort(key=len, reverse=True)
        self._ci = re.compile(r"(?<!\w)(" + "|".join(ci_parts) + r")(?!\w)",
                              re.IGNORECASE) if ci_parts else None
        self._cs = re.compile(r"(?<!\w)(" + "|".join(cs_parts) + r")(?!\w)") \
            if cs_parts else None

    @classmethod
    def from_csv(cls, path) -> "EntityLexicon":
        out = []
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(
                    r for r in f if not r.lstrip().startswith("#")):
                hid = (row.get("hltv_id") or "").strip()
                out.append(Entity(
                    entity_id=row["entity_id"].strip(),
                    kind=row["kind"].strip(),
                    canonical=row["canonical"].strip(),
                    aliases=tuple(a.strip() for a in
                                  (row.get("aliases") or "").split("|") if a.strip()),
                    ambiguous=(row.get("ambiguous") or "0").strip() in ("1", "true"),
                    hltv_id=int(hid) if hid else None))
        return cls(out)

    def resolve_text(self, text: str) -> list[TextMention]:
        """All entity mentions in free text; one mention per entity per call
        (repeat mentions in one comment are hype, not new information)."""
        if not text:
            return []
        seen: dict[str, TextMention] = {}
        if self._ci is not None:
            for m in self._ci.finditer(text):
                e = self._alias_owner.get(m.group(1).lower())
                if e and e.entity_id not in seen:
                    seen[e.entity_id] = TextMention(e.entity_id, m.group(1), "text")
        if self._cs is not None:
            for m in self._cs.finditer(text):
                e = self._alias_owner.get(m.group(1))
                if e and e.entity_id not in seen:
                    seen[e.entity_id] = TextMention(e.entity_id, m.group(1), "text")
        return list(seen.values())

    _HREF = re.compile(r"/(player|team)/(\d+)/")

    def resolve_hltv_href(self, href: str, layer: str = "link") -> TextMention | None:
        """Resolve an HLTV /player/<id>/ or /team/<id>/ href — id-based,
        collision-proof (design decision 3)."""
        m = self._HREF.search(href or "")
        if not m:
            return None
        kind = "player" if m.group(1) == "player" else "team"
        e = self._by_hltv.get((kind, int(m.group(2))))
        return TextMention(e.entity_id, href, layer) if e else None
