"""给钉钉发消息。

- 回复刚收到的消息：优先用回调里带的 sessionWebhook（不用换令牌，群聊单聊都行，约 1.5 小时内有效）；
  地址只接受钉钉自己的域名（防止被伪造成任意地址去请求内网）。
- 主动发（卡片按钮的执行结果等）：机器人单聊批量发送 /v1.0/robot/oToMessages/batchSend，需要配置 robotCode。
- 卡片：配置了互动卡片模板（card_template_id）就发互动卡片（createAndDeliver），按钮回调带 {action, token}；
  没配置就发 Markdown，说明“请到网页工作台确认”，并附上网页链接（INTEGRATION_WEB_BASE_URL）。
"""
import json
import time
import uuid
from typing import Any, Dict
from urllib.parse import urlparse

from service.integrations.base import BusinessCard, IntegrationError
from service.integrations.dingtalk import client
from service.integrations.dingtalk.cards import DingTalkCardRenderer
from service.integrations.http import call_json

MAX_TEXT = 5000


def _webhook(context: Dict[str, Any]) -> str:
    url = str(context.get("session_webhook") or "")
    if not url:
        return ""
    expires = context.get("session_webhook_expires")
    if expires and float(expires) / 1000 < time.time() + 5:
        return ""
    allowed = {urlparse(client.oapi_url()).hostname, urlparse(client.base_url()).hostname}
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or parsed.hostname not in allowed:
        return ""
    return url


def _robot_send(app, context: Dict[str, Any], msg_key: str, msg_param: Dict[str, Any]) -> None:
    staff_id = context.get("staff_id")
    if not staff_id:
        raise IntegrationError("不知道发给谁（缺少员工 userId）")
    if not app.robot_code:
        raise IntegrationError("没有配置机器人 robotCode，不能主动发消息")
    client.call(app, "POST", "/v1.0/robot/oToMessages/batchSend",
                json_body={"robotCode": app.robot_code, "userIds": [staff_id], "msgKey": msg_key,
                           "msgParam": json.dumps(msg_param, ensure_ascii=False)})


def send_text(app, context: Dict[str, Any], text: str) -> None:
    text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n……（内容过长，完整内容请在网页工作台查看）"
    webhook = _webhook(context)
    if webhook:
        data = call_json("dingtalk", "POST", webhook, json_body={"msgtype": "text", "text": {"content": text}})
        if data.get("errcode") not in (0, None):
            raise IntegrationError(f"钉钉返回错误 {data.get('errcode')}：{str(data.get('errmsg') or '')[:200]}")
        return
    _robot_send(app, context, "sampleText", {"content": text})


def send_card(app, context: Dict[str, Any], card: BusinessCard) -> None:
    if app.card_template_id and context.get("staff_id") and app.robot_code:
        client.call(app, "POST", "/v1.0/card/instances/createAndDeliver", json_body={
            "cardTemplateId": app.card_template_id, "outTrackId": uuid.uuid4().hex, "callbackType": "HTTP",
            "cardData": {"cardParamMap": DingTalkCardRenderer.render(card)},
            "openSpaceId": f"dtv1.card//IM_ROBOT.{context['staff_id']}",
            "imRobotOpenSpaceModel": {"supportForward": False},
            "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT", "robotCode": app.robot_code}})
        return
    title, text = DingTalkCardRenderer.markdown(card)
    webhook = _webhook(context)
    if webhook:
        data = call_json("dingtalk", "POST", webhook, json_body={"msgtype": "markdown", "markdown": {"title": title, "text": text}})
        if data.get("errcode") not in (0, None):
            raise IntegrationError(f"钉钉返回错误 {data.get('errcode')}：{str(data.get('errmsg') or '')[:200]}")
        return
    _robot_send(app, context, "sampleMarkdown", {"title": title, "text": text})
