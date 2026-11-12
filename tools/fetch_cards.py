"""从游戏内图鉴抓取铁甲战士卡牌数值, 存成模拟器用的数据文件."""
import json
import os
import urllib.parse
import urllib.request

NAMES = [
    "打击", "防御", "痛击", "上勾拳", "战栗", "耸肩无视", "双重打击", "连续反弹",
    "飞剑回旋镖", "旋风斩", "沉重打击", "全身撞击", "欺凌", "灰烬打击", "契约终章",
    "恶魔形态", "燃烧", "烙印", "腐化", "黑暗之拥", "无痛", "壁垒", "主宰",
    "不可动摇", "破裂", "残酷", "凶恶",
]


def fetch(name: str) -> dict | None:
    url = "http://127.0.0.1:15526/api/v1/wiki?query=" + urllib.parse.quote(name)
    with urllib.request.urlopen(url, timeout=10) as response:
        data = json.load(response)
    for item in data.get("results") or []:
        if item.get("item_type") == "card" and item.get("name") == name:
            return item
    return None


def main() -> None:
    out = {}
    for name in NAMES:
        try:
            card = fetch(name)
        except Exception as error:  # noqa: BLE001
            print("ERR", name, error)
            continue
        if not card:
            print("MISS", name)
            continue
        out[name] = {"id": card["id"], "base": card["base"], "upgraded": card["upgraded"]}
        print("OK", name, card["base"]["cost"], card["base"]["description"][:40])
    os.makedirs("data/cards", exist_ok=True)
    with open("data/cards/ironclad.json", "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False, indent=1)
    print("saved", len(out))


if __name__ == "__main__":
    main()
