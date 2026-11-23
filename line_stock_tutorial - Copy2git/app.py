# -*- coding: utf-8 -*-
"""台股 LINE Bot（修改版）

指令：
  me        -> 顯示你的 LINE user id
  profile   -> 顯示你的 LINE 個人資料
  #2330     -> 即時股價 + 最近五個交易日收盤價
  /2330     -> 收盤價走勢圖

需要的環境變數：
  LINE_CHANNEL_SECRET
  LINE_CHANNEL_ACCESS_TOKEN
  BASE_URL   (ngrok 的 https 網址，例如 https://xxxx.ngrok-free.dev，結尾不要加 /)
"""
import os
import re
import time
from datetime import datetime

import matplotlib
matplotlib.use('Agg')  # 不開視窗，只存檔
import matplotlib.pyplot as plt
import pandas as pd
import twstock

from flask import Flask, request, abort
from linebot import LineBotApi, WebhookParser
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageSendMessage
)

app = Flask(__name__)

# ---------- 設定（從環境變數讀取，不要把金鑰寫死在程式裡）----------
CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
parser = WebhookParser(CHANNEL_SECRET)

# 圖表存在 static 資料夾，Flask 會自動在 /static/檔名 提供下載
STATIC_DIR = os.path.join(app.root_path, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# 股票代號只允許 4~6 碼英數字，避免使用者輸入奇怪字元（尤其是拿來當檔名）
STOCK_ID_PATTERN = re.compile(r"^[0-9A-Za-z]{4,6}$")

HELP_TEXT = (
    "可用指令：\n"
    "#2330  即時股價與近五日收盤價\n"
    "/2330  收盤價走勢圖\n"
    "me  顯示你的 user id\n"
    "profile  顯示你的個人資料"
)


# ---------- 功能函式 ----------
def build_realtime_text(stock_id):
    """即時股價 + 最近五個交易日收盤價，回傳文字。"""
    rt = twstock.realtime.get(stock_id)
    if not rt.get("success"):
        return "查不到 %s 的即時資料：%s" % (stock_id, rt.get("rtmessage", "未知錯誤"))

    # timestamp 是 UTC 秒數；本機是台灣時區，直接轉換即可（不需再加 8 小時）
    time_str = datetime.fromtimestamp(rt["timestamp"]).strftime("%H:%M:%S")
    info = rt["info"]
    r = rt["realtime"]

    lines = [
        "%s (%s) %s" % (info["name"], info["code"], time_str),
        "現價: %s / 開盤: %s" % (r["latest_trade_price"], r["open"]),
        "最高: %s / 最低: %s" % (r["high"], r["low"]),
        "成交量: %s" % r["accumulate_trade_volume"],
        "-----",
        "最近五個交易日收盤價:",
    ]

    stock = twstock.Stock(stock_id)
    prices = stock.price[-5:][::-1]
    dates = stock.date[-5:][::-1]
    for d, p in zip(dates, prices):
        lines.append("[%s] %s" % (d.strftime("%Y-%m-%d"), p))
    return "\n".join(lines)


def build_chart(stock_id):
    """畫收盤價走勢圖，存到 static，回傳檔名。"""
    stock = twstock.Stock(stock_id)
    df = pd.DataFrame({"date": stock.date, "close": stock.close})

    filename = "%s_%d.png" % (stock_id, int(time.time()))
    path = os.path.join(STATIC_DIR, filename)

    df.plot(x="date", y="close")
    plt.title("[%s]" % stock.sid)
    plt.savefig(path)
    plt.close()
    return filename


# ---------- Webhook ----------
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        events = parser.parse(body, signature)
    except InvalidSignatureError:
        abort(400)

    for event in events:
        if not isinstance(event, MessageEvent):
            continue
        if not isinstance(event.message, TextMessage):
            continue

        text = event.message.text.strip()
        try:
            handle_text(event, text)
        except Exception:
            app.logger.exception("處理訊息時發生錯誤")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="查詢失敗，請稍後再試或確認股票代號。"),
            )

    return "OK"


def handle_text(event, text):
    if text.lower() == "me":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=str(event.source.user_id)),
        )

    elif text.lower() == "profile":
        profile = line_bot_api.get_profile(event.source.user_id)
        line_bot_api.reply_message(
            event.reply_token,
            [
                TextSendMessage(text="Display name: " + profile.display_name),
                TextSendMessage(text="picture url: " + (profile.picture_url or "-")),
                TextSendMessage(text="status_message: " + (profile.status_message or "-")),
            ],
        )

    elif text.startswith("#") or text.startswith("/"):
        command, stock_id = text[0], text[1:].strip()
        if not STOCK_ID_PATTERN.match(stock_id):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="股票代號格式不正確，例如：#2330"),
            )
            return

        if command == "#":
            content = build_realtime_text(stock_id)
            line_bot_api.reply_message(
                event.reply_token, TextSendMessage(text=content)
            )
        else:
            if not BASE_URL:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="尚未設定 BASE_URL，無法回傳圖片。"),
                )
                return
            filename = build_chart(stock_id)
            url = "%s/static/%s" % (BASE_URL, filename)
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(original_content_url=url, preview_image_url=url),
            )

    else:
        line_bot_api.reply_message(
            event.reply_token, TextSendMessage(text=HELP_TEXT)
        )


@app.route("/", methods=["GET"])
def basic_url():
    return "OK"


if __name__ == "__main__":
    app.run(port=5000)