"""项目内的 Skill 机制: 目录 + 说明书 + 数据, 按局面命中才注入.

我们本身也是一个 Agent, 所以知识不该写死在代码里: 加一个技能只需要建目录写
SKILL.md, Agent 在事件/牌组命中时把它注入提示词, 没命中就不占篇幅。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain.skills import Skill, discover, load_skill, matching

SKILL_MD = """---
name: demo
description: 演示技能
events: 测试事件
cards: 飞剑回旋镖
screens: shop
---
正文说明。
"""


def write_skill(root: Path, folder: str, text: str) -> None:
    target = root / folder
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(text, encoding="utf-8")


class ParseTests(unittest.TestCase):
    def test_frontmatter_is_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo"
            path.mkdir()
            (path / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
            skill = load_skill(path / "SKILL.md")
        self.assertEqual(skill.name, "demo")
        self.assertEqual(skill.description, "演示技能")
        self.assertEqual(skill.triggers["events"], ("测试事件",))
        self.assertEqual(skill.triggers["cards"], ("飞剑回旋镖",))
        self.assertIn("正文说明", skill.body)

    def test_file_without_frontmatter_is_all_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain"
            path.mkdir()
            (path / "SKILL.md").write_text("没有 frontmatter。", encoding="utf-8")
            skill = load_skill(path / "SKILL.md")
        self.assertEqual(skill.name, "plain")
        self.assertEqual(skill.triggers, {})
        self.assertIn("没有 frontmatter", skill.body)


class MatchTests(unittest.TestCase):
    def _skill(self, **triggers):
        return Skill(name="demo", description="", triggers=triggers,
                     body="", path=None)

    def test_matches_by_event(self):
        skill = self._skill(events=("脑蛭",))
        self.assertTrue(skill.matches(event_name="脑蛭"))
        self.assertFalse(skill.matches(event_name="巨大花卉"))

    def test_matches_by_card_with_upgrade_suffix(self):
        skill = self._skill(cards=("飞剑回旋镖",))
        self.assertTrue(skill.matches(card_names=["飞剑回旋镖+"]))
        self.assertFalse(skill.matches(card_names=["打击"]))

    def test_matches_by_screen(self):
        skill = self._skill(screens=("shop",))
        self.assertTrue(skill.matches(screen="shop"))
        self.assertFalse(skill.matches(screen="map"))


class DiscoveryTests(unittest.TestCase):
    def test_discovers_skills_and_skips_broken_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_skill(root, "a", SKILL_MD)
            write_skill(root, "b", "---\ndescription: 没有 name\n---\n正文\n")
            skills = discover(root)
        names = {skill.name for skill in skills}
        self.assertIn("demo", names)
        self.assertIn("b", names)     # 没有 name 时用目录名

    def test_matching_filters_by_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_skill(root, "a", SKILL_MD)
            self.assertEqual(len(matching(event_name="测试事件", root=root)), 1)
            self.assertEqual(len(matching(event_name="别的事件", root=root)), 0)

    def test_missing_root_returns_empty(self):
        self.assertEqual(discover(Path("definitely-not-here")), [])


class ProjectSkillTests(unittest.TestCase):
    def test_the_bundled_knowledge_skill_is_discoverable(self):
        skills = discover()
        self.assertIn("sts2-knowledge", {skill.name for skill in skills})
        skill = next(skill for skill in skills if skill.name == "sts2-knowledge")
        self.assertIn("特兹卡塔拉", skill.triggers["events"])
        self.assertIn("飞剑回旋镖", skill.triggers["cards"])
        data = skill.path / "data"
        for name in ("events.json", "ancients.json", "combos.json"):
            self.assertTrue((data / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
