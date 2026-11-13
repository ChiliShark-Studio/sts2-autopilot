"""项目内的 Skill 机制: 目录 + 说明书 + 数据, 按局面命中才注入.

结构:

    skills/<name>/SKILL.md        说明书, 开头是 frontmatter:
                                  ---
                                  name: sts2-knowledge
                                  description: 事件与先古遗物的选择数据
                                  events: 涅奥, 特兹卡塔拉        # 事件名触发
                                  cards: 飞剑回旋镖, 残酷          # 牌名触发
                                  screens: shop                   # 界面触发
                                  ---
                                  正文: 给模型看的说明
    skills/<name>/data/*.json     该技能的数据文件 (可选)

设计目标: 知识从代码里剥离。加一个新技能只需要建目录写 SKILL.md,
不需要改 Agent 代码; 没命中的技能不会进入提示词。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills"   # 仓库根目录下的 skills/

# 触发条件字段 (frontmatter 里的键 -> 含义).
_TRIGGER_KEYS = ("events", "cards", "screens", "ancients")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    body: str = ""
    path: Path | None = None

    def matches(self, *, event_name: str = "", card_names=(), screen: str = "") -> bool:
        """任一触发条件命中即可."""
        triggers = self.triggers
        events = triggers.get("events", ()) + triggers.get("ancients", ())
        if event_name and any(trigger in event_name or event_name in trigger
                              for trigger in events):
            return True
        if screen and screen in triggers.get("screens", ()):
            return True
        cards = {name.rstrip("+") for name in card_names if name}
        if cards & {name.rstrip("+") for name in triggers.get("cards", ())}:
            return True
        return False

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "body": self.body}


def _parse(text: str) -> tuple[dict[str, str], str]:
    """拆出 frontmatter 与正文; 没有 frontmatter 时整篇都是正文."""
    if not text.startswith("---"):
        return {}, text.strip()
    _, _, rest = text.partition("\n")
    meta_lines = []
    body_lines = []
    in_meta = True
    for line in rest.splitlines():
        if in_meta and line.strip() == "---":
            in_meta = False
            continue
        (meta_lines if in_meta else body_lines).append(line)
    meta = {}
    for line in meta_lines:
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, "\n".join(body_lines).strip()


def load_skill(path: Path) -> Skill | None:
    """读取单个技能的 SKILL.md; 没有 name 的文件忽略."""
    try:
        meta, body = _parse(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    name = meta.get("name") or path.parent.name
    if not name:
        return None
    triggers = {
        key: tuple(part.strip() for part in meta.get(key, "").split(",") if part.strip())
        for key in _TRIGGER_KEYS if meta.get(key)
    }
    return Skill(name=name, description=meta.get("description", ""),
                 triggers=triggers, body=body, path=path.parent)


def discover(root: Path | None = None) -> list[Skill]:
    """扫描 skills/*/SKILL.md."""
    base = root or ROOT
    if not base.is_dir():
        return []
    skills = []
    for path in sorted(base.glob("*/SKILL.md")):
        skill = load_skill(path)
        if skill is not None:
            skills.append(skill)
    return skills


def matching(*, event_name: str = "", card_names=(), screen: str = "",
             root: Path | None = None) -> list[Skill]:
    """返回当前局面命中触发条件的技能."""
    return [skill for skill in discover(root)
            if skill.matches(event_name=event_name, card_names=card_names, screen=screen)]
