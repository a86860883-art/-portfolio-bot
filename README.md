# 持股健檢 Bot（截圖辨識版）

LINE 聊天機器人，不需要嘉信 API，透過截圖辨識取得持股資料。

## 專案結構

```
portfolio-healthcheck/
├── app.py                    主伺服器
├── requirements.txt
├── Procfile                  Railway 部署
├── .env.example              環境變數範本
├── sources/
│   ├── screenshot_ocr.py     Claude Vision 截圖辨識
│   ├── holdings_store.py     持股 JSON 快取
│   ├── stocktwits.py         社群情緒（免費）
│   ├── news.py               Yahoo Finance + Google News
│   └── sec_edgar.py          SEC 重大申報（免費官方）
├── analyzers/
│   ├── technical.py          技術指標（RSI/MACD/布林/MA）
│   └── ai_summary.py         Claude AI 報告（含盲點提示）
├── scheduler/
│   └── daily.py              每日 05:30 自動健檢
└── notifier/
    ├── line_push.py           LINE 推播
    └── dashboard.py           Flex Message 儀表板
```

## 快速開始

### 步驟一：安裝套件
```bash
pip install -r requirements.txt
pip install ta
```

### 步驟二：建立 .env
```bash
cp .env.example .env
# 用記事本開啟 .env 填入金鑰
```

### 步驟三：本機測試
```bash
# 終端機 1：啟動伺服器
uvicorn app:app --reload --port 8000

# 終端機 2：建立公開網址
ngrok http 8000
```

### 步驟四：設定 LINE Webhook
```
https://（ngrok網址）/webhook
```

### 步驟五：部署到 Railway
1. 推上 GitHub
2. Railway → New Project → Deploy from GitHub
3. 在 Variables 填入 .env 的所有變數
4. Settings → Domains 取得網址
5. 更新 LINE Webhook URL

## LINE 指令

| 指令 | 功能 |
|------|------|
| 傳截圖 | 自動辨識持股並更新 |
| `/menu` | 互動儀表板 |
| `/holdings` | 持股清單與損益 |
| `/report` | 今日健檢報告 |
| `/technical` | 技術分析訊號 |
| `/sentiment` | 社群情緒 |
| `/status` | 資料更新狀態 |
| `/reset` | 清除對話記憶 |

## 使用方式

1. 打開嘉信 App 或網頁版，進入持股頁面
2. 截圖後直接傳給 LINE Bot
3. Bot 自動辨識持股資料並儲存
4. 建議每週更新一次截圖
5. 每天凌晨 5:30 自動收到健檢報告

## 免責聲明

本工具僅供個人學習與資訊參考，不構成投資建議。
