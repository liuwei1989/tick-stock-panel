"""Additional opt-in notification adapters; credentials never appear in responses.

Existing Feishu/WeCom/SMTP/custom adapters remain the authoritative old paths.
New channels share config metadata, bounded dispatch, wire validation and audit.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from urllib.parse import quote, urlencode, urlsplit

import httpx

from app import secrets_store
from app.services.json_report_store import JsonReportStore

# Required fields are also the settings UI contract. URL and IDs may themselves
# be credentials, so all fields are stored in secrets.json and are write-only.
CHANNELS = {
    "dingtalk": ("钉钉", ["url"], ["secret"]),
    "telegram": ("Telegram", ["token", "chat_id"], []),
    "discord": ("Discord", ["url"], []),
    "slack": ("Slack", ["url"], []),
    "gotify": ("Gotify", ["url", "token"], []),
    "ntfy": ("ntfy", ["url"], ["token"]),
    "pushover": ("Pushover", ["token", "user"], []),
    "pushplus": ("PushPlus", ["token"], []),
    "serverchan3": ("Server酱3", ["sendkey"], []),
    "astrbot": ("AstrBot", ["url"], ["token"]),
}
ALL_CHANNEL_IDS = ("feishu", "wecom", "custom", "email", *CHANNELS)
_audit = JsonReportStore("notification_deliveries.json", 500, "delivery", id_with_symbol=False)
_lock = threading.Lock()
_pending = threading.BoundedSemaphore(64)
_recent: OrderedDict[str, float] = OrderedDict()


def configs():
    return {
        k: v
        for k, v in (secrets_store.load().get("notification_channels") or {}).items()
        if k in CHANNELS and isinstance(v, dict)
    }


def channel_status():
    configured = configs()
    return [
        {
            "id": key,
            "label": label,
            "fields": required + optional,
            "required_fields": required,
            "configured": all(configured.get(key, {}).get(f) for f in required),
            "set_fields": [f for f in required + optional if configured.get(key, {}).get(f)],
        }
        for key, (label, required, optional) in CHANNELS.items()
    ]


def save_config(channel, updates):
    if channel not in CHANNELS:
        raise ValueError("未知通知渠道")
    _label, required, optional = CHANNELS[channel]
    if any(k not in required + optional for k in updates):
        raise ValueError("未知配置字段")
    if any(not isinstance(v, str) or len(v) > 4000 for v in updates.values()):
        raise ValueError("无效配置字段")
    with _lock:
        value = configs()
        updated = {**value.get(channel, {}), **updates}
        if updated.get("url"):
            _url(updated["url"])
        value[channel] = updated
        secrets_store.save({"notification_channels": value})


def _url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("通知地址必须是 HTTP(S) URL, 不允许内嵌账户密码")
    return value


def build_request(channel, config, title, body, *, timestamp=None):
    if channel not in CHANNELS:
        raise ValueError("未知通知渠道")
    title = title[:100]
    # Bound by UTF-8 bytes as well as characters (Chinese/emoji payloads).
    limit = 1400 if channel in {"discord", "pushover"} else 3000
    text = title + "\n" + body
    if len(text.encode()) > limit:
        text = (
            text.encode()[: limit - 80].decode("utf-8", errors="ignore") + "\n…完整内容请查看工作台"
        )
    url = config.get("url", "")
    if channel == "telegram":
        url = "https://api.telegram.org/bot" + quote(config["token"], safe=":") + "/sendMessage"
        kwargs = {"json": {"chat_id": config["chat_id"], "text": text}}
    elif channel == "dingtalk":
        if config.get("secret"):
            stamp = str(timestamp if timestamp is not None else round(time.time() * 1000))
            secret = config["secret"]
            sign = base64.b64encode(
                hmac.new(secret.encode(), f"{stamp}\n{secret}".encode(), hashlib.sha256).digest()
            ).decode()
            url += ("&" if "?" in url else "?") + urlencode({"timestamp": stamp, "sign": sign})
        kwargs = {"json": {"msgtype": "markdown", "markdown": {"title": title, "text": text}}}
    elif channel == "discord":
        kwargs = {"json": {"content": text, "allowed_mentions": {"parse": []}}}
    elif channel == "slack":
        kwargs = {
            "json": {"text": text, "mrkdwn": False, "unfurl_links": False, "unfurl_media": False}
        }
    elif channel == "gotify":
        url = url.rstrip("/") + "/message"
        kwargs = {
            "headers": {"X-Gotify-Key": config["token"]},
            "json": {"title": title, "message": text, "priority": 5},
        }
    elif channel == "ntfy":
        kwargs = {
            "content": text.encode("utf-8"),
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
        }
        if config.get("token"):
            kwargs["headers"]["Authorization"] = "Bearer " + config["token"]
    elif channel == "pushover":
        url = "https://api.pushover.net/1/messages.json"
        kwargs = {
            "data": {
                "token": config["token"],
                "user": config["user"],
                "title": title,
                "message": text,
            }
        }
    elif channel == "pushplus":
        url = "https://www.pushplus.plus/send"
        kwargs = {
            "json": {"token": config["token"], "title": title, "content": text, "template": "txt"}
        }
    elif channel == "serverchan3":
        key = config["sendkey"]
        match = re.match(r"^sctp(\d+)t", key)
        if key.startswith("sctp") and not match:
            raise ValueError("无效 SendKey")
        url = (
            f"https://{match.group(1)}.push.ft07.com/send/{quote(key, safe='')}.send"
            if match
            else f"https://sctapi.ftqq.com/{quote(key, safe='')}.send"
        )
        kwargs = {"json": {"title": title, "desp": text}}
    else:  # AstrBot webhook protocol used by upstream DSA
        payload = {"content": "<pre>" + html.escape(text) + "</pre>"}
        stamp = str((timestamp if timestamp is not None else round(time.time() * 1000)) // 1000)
        signature = (
            hmac.new(
                config.get("token", "").encode(),
                f"{stamp}.{json.dumps(payload, sort_keys=True)}".encode(),
                hashlib.sha256,
            ).hexdigest()
            if config.get("token")
            else ""
        )
        kwargs = {"json": payload, "headers": {"X-Timestamp": stamp, "X-Signature": signature}}
    return _url(url), kwargs


def deliver(channel, config, title, body, *, send=None, sleep=time.sleep):
    send = send or httpx.post
    for attempt in range(1, 4):
        try:
            url, kwargs = build_request(channel, config, title, body)
            response = send(url, **kwargs, timeout=8.0, follow_redirects=False)
            code = response.status_code
            if 200 <= code < 300:
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                if channel == "slack":
                    ok = response.text.strip() == "ok"
                elif channel == "telegram":
                    ok = data.get("ok") is True
                elif channel == "dingtalk":
                    ok = data.get("errcode") == 0
                elif channel == "pushover":
                    ok = data.get("status") == 1
                elif channel == "pushplus":
                    ok = data.get("code") == 200
                elif channel == "serverchan3":
                    ok = data.get("code") == 0
                elif channel == "gotify":
                    ok = bool(data.get("id"))
                else:
                    ok = True
                return {
                    "status": "sent" if ok else "failed",
                    "attempts": attempt,
                    "reason": None if ok else "provider_rejected",
                }
            if code != 429 and code < 500:
                return {"status": "failed", "attempts": attempt, "reason": f"http_{code}"}
        except (KeyError, ValueError):
            return {"status": "failed", "attempts": attempt, "reason": "invalid_configuration"}
        except Exception:
            pass  # Never persist raw exceptions: HTTP errors can include secret URLs.
        if attempt < 3:
            sleep(2 ** (attempt - 1))
    return {"status": "failed", "attempts": 3, "reason": "transport_unavailable"}


def dispatch(executor, channel, title, body, *, event_key):
    config = configs().get(channel, {})
    if channel not in CHANNELS or not all(config.get(k) for k in CHANNELS[channel][1]):
        return False
    key = hashlib.sha256((channel + "|" + event_key).encode()).hexdigest()
    with _lock:
        now = time.monotonic()
        if key in _recent and now - _recent[key] < 60:
            return False
        if not _pending.acquire(blocking=False):
            return False
        _recent[key] = now
        while len(_recent) > 1000:
            _recent.popitem(last=False)

    def worker():
        try:
            result = deliver(channel, config, title, body)
            _audit.save_report(
                {
                    "id": "delivery_" + uuid.uuid4().hex,
                    "channel": channel,
                    "event_key": key,
                    **result,
                }
            )
            if result["status"] != "sent":
                with _lock:
                    _recent.pop(key, None)
        finally:
            _pending.release()

    try:
        executor.submit(worker)
    except Exception:
        _pending.release()
        with _lock:
            _recent.pop(key, None)
        return False
    return True


def delivery_history():
    return _audit.list_reports()
