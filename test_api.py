"""
API 連線測試端點 - 部署後訪問 /test 確認能否連到 Anthropic
"""
import os
import httpx
from fastapi import APIRouter

router = APIRouter()

@router.get("/test")
async def test_connections():
    results = {}

    # 測試 Anthropic API
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        if resp.status_code == 200:
            results["anthropic"] = "SUCCESS - 可以連線"
        else:
            results["anthropic"] = f"FAIL - HTTP {resp.status_code}: {resp.text[:100]}"
    except Exception as e:
        results["anthropic"] = f"FAIL - {type(e).__name__}: {str(e)[:100]}"

    # 測試 Gemini API
    try:
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json={
                    "contents": [{"parts": [{"text": "hi"}]}],
                    "generationConfig": {"maxOutputTokens": 10}
                })
            if resp.status_code == 200:
                results["gemini"] = "SUCCESS - 可以連線"
            else:
                results["gemini"] = f"FAIL - HTTP {resp.status_code}: {resp.text[:100]}"
        else:
            results["gemini"] = "SKIP - 未設定 GEMINI_API_KEY"
    except Exception as e:
        results["gemini"] = f"FAIL - {type(e).__name__}: {str(e)[:100]}"

    return results
