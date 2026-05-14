"""
Rich Menu 設定模組
執行 python -m notifier.rich_menu 一次性設定 LINE Rich Menu
"""
import os
import json
import httpx
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
BASE  = "https://api.line.me/v2/bot"
HDR   = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


RICH_MENU_DEF = {
    "size": {"width": 2500, "height": 843},
    "selected": True,
    "name": "持股健檢選單",
    "chatBarText": "功能選單",
    "areas": [
        {
            "bounds": {"x": 0, "y": 0, "width": 833, "height": 421},
            "action": {"type": "message", "text": "/overview"}
        },
        {
            "bounds": {"x": 833, "y": 0, "width": 833, "height": 421},
            "action": {"type": "message", "text": "/detail"}
        },
        {
            "bounds": {"x": 1666, "y": 0, "width": 834, "height": 421},
            "action": {"type": "message", "text": "/news"}
        },
        {
            "bounds": {"x": 0, "y": 421, "width": 833, "height": 422},
            "action": {"type": "message", "text": "/holdings"}
        },
        {
            "bounds": {"x": 833, "y": 421, "width": 833, "height": 422},
            "action": {"type": "message", "text": "/sentiment"}
        },
        {
            "bounds": {"x": 1666, "y": 421, "width": 834, "height": 422},
            "action": {"type": "message", "text": "/balance"}
        },
    ]
}


async def setup_rich_menu(image_path: str = "rich_menu.png"):
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        # 1. 建立 Rich Menu
        r = await client.post(f"{BASE}/richmenu", headers=HDR,
                              json=RICH_MENU_DEF)
        r.raise_for_status()
        menu_id = r.json()["richMenuId"]
        print(f"Rich Menu 建立成功：{menu_id}")

        # 2. 上傳圖片
        with open(image_path, "rb") as f:
            img_data = f.read()
        r = await client.post(
            f"{BASE}/richmenu/{menu_id}/content",
            headers={"Authorization": f"Bearer {TOKEN}",
                     "Content-Type": "image/png"},
            content=img_data,
        )
        r.raise_for_status()
        print("圖片上傳成功")

        # 3. 設為預設選單
        r = await client.post(
            f"{BASE}/user/all/richmenu/{menu_id}",
            headers=HDR,
        )
        r.raise_for_status()
        print("已設為預設 Rich Menu，完成！")
        return menu_id


if __name__ == "__main__":
    import asyncio
    asyncio.run(setup_rich_menu())
