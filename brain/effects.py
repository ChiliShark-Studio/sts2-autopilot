"""Turn the bridge's Chinese UI strings back into numbers.

This is a **stopgap**, and it is worth being explicit about why: the game knows
the exact damage/block numbers, but the mod only renders them into text.  So we
regex them out.  It works for the starter deck and most base cards; it will
misread cards whose text is conditional ("如果...") or scaled ("X 点").

The proper fix is a mod-side patch that adds numeric fields next to the text.
When that lands, ``parse_card`` can prefer the structured fields and keep the
parser only as a fallback -- the planner does not care where the numbers came
from.
"""

from __future__ import annotations

import re
from typing import Any

# 造成 6 点伤害 / 造成3点伤害3次 / 造成 8 点伤害。 给予 2 层易伤。
_DAMAGE = re.compile(r"造成\s*(\d+)\s*点伤害(?:[\s，,]*(\d+)\s*次)?")
_BLOCK = re.compile(r"获得\s*(\d+)\s*点格挡")
_VULN = re.compile(r"给予\s*(\d+)\s*层易伤")
_WEAK = re.compile(r"给予\s*(\d+)\s*层虚弱")
_DRAW = re.compile(r"抽\s*(\d+)\s*张牌")
_ENERGY = re.compile(r"获得\s*(\d+)\s*点能量")
_HEAL = re.compile(r"回复\s*(\d+)\s*点(?:生命|最大生命值)")
_STRENGTH_GAIN = re.compile(r"获得\s*(\d+)\s*点力量")


def parse_potion(description: str) -> dict[str, int]:
    """Turn a potion's rendered text into numbers.

    Potions only expose a description string, so this is the same stopgap as
    card parsing: good enough for the damage/block/heal potions that matter for
    survival and finishing, and 0 for anything fancier.
    """
    desc = description or ""

    def _first(pattern: re.Pattern[str]) -> int:
        match = pattern.search(desc)
        return _int(match.group(1)) if match else 0

    return {
        "damage": _first(_DAMAGE),
        "block": _first(_BLOCK),
        "heal": _first(_HEAL),
        "strength": _first(_STRENGTH_GAIN),
        "vulnerable": _first(_VULN),
        "weak": _first(_WEAK),
        "draw": _first(_DRAW),
        # Card-enhancement potions (e.g. 熔炉的祝福) change the hand itself;
        # their value is computed from card scores, not from a number.
        "upgrade_hand": 1 if ("升级" in desc and "手牌" in desc) else 0,
        # Duplication: "本回合，你打出的下一张牌会额外打出一次。"
        "duplicate_next": 1 if ("额外打出一次" in desc or "打出两次" in desc) else 0,
    }


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _field(card: dict[str, Any], key: str, pattern: re.Pattern[str]) -> int:
    """Structured field first, description text as the fallback.

    The patched mod always emits the key (0 when the card has no such var), so
    presence -- not truthiness -- decides.  Trusting a 0 is important: a card
    whose text mentions a number in a conditional must not have it scraped.
    """
    if key in card and card[key] is not None:
        return _int(card[key])
    m = pattern.search(card.get("description") or "")
    return _int(m.group(1)) if m else 0


def parse_card(card: dict[str, Any]) -> dict[str, Any]:
    """Extract numbers from one hand card.

    The mod exposes ``damage``/``block``/``hits``/``vulnerable``/``weak`` from
    the game's own DynamicVars; older builds only had ``description`` and the
    parser below still covers them.
    """
    desc = card.get("description") or ""

    damage = _field(card, "damage", _DAMAGE)
    block = _field(card, "block", _BLOCK)
    hits = _int(card.get("hits"), 0) if "hits" in card else 0

    if "damage" not in card:
        m = _DAMAGE.search(desc)
        if m and m.group(2):
            hits = _int(m.group(2))
    hits = max(1, hits or 1)

    return {
        "index": _int(card.get("index")),
        "id": card.get("id") or "",
        "name": card.get("name") or "",
        "cost": _int(card.get("cost"), 1),
        "type": (card.get("type") or "").lower(),
        "target_type": card.get("target_type") or "",
        # Star cost is the Regent's resource; we do not model stars, so a card
        # that costs them must be treated as unplayable rather than attempted.
        "star_cost": _int(card.get("star_cost"), 0),
        "can_play": bool(card.get("can_play", True)),
        "damage": damage,
        "hits": hits,
        "block": block,
        "vulnerable": _field(card, "vulnerable", _VULN),
        "weak": _field(card, "weak", _WEAK),
        "draw": _field(card, "draw", _DRAW),
        "energy": _field(card, "energy", _ENERGY),
        "unplayable_reason": card.get("unplayable_reason"),
    }


def parse_intent(intent: dict[str, Any]) -> tuple[int, int]:
    """(damage, hits) for an enemy intent."""
    if intent.get("damage") is not None:
        return _int(intent.get("damage")), max(1, _int(intent.get("hits"), 1))

    if (intent.get("type") or "").lower() != "attack":
        return 0, 1

    label = str(intent.get("label") or "").strip()
    if label.isdigit():
        return _int(label), 1

    m = _DAMAGE.search(intent.get("description") or "")
    if m:
        return _int(m.group(1)), (_int(m.group(2), 1) if m.group(2) else 1)
    return 0, 1


def incoming_damage(enemy: dict[str, Any]) -> int:
    total = 0
    for intent in enemy.get("intents") or []:
        dmg, hits = parse_intent(intent)
        total += dmg * hits
    return total
