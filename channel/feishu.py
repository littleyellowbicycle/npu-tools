#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import logging
import threading
import requests as http_requests

from lark_oapi import Client as LarkClient, EventDispatcherHandler
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    CreateMessageRequest,
    CreateMessageRequestBody,
)
from lark_oapi.ws import Client as WSClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_servers, get_feishu_config
from services.npu_service import query_npu_status, generate_status_image

log = logging.getLogger("NPU-Tools.Channel.Feishu")

LARK_CLIENT = None
TOKEN_CACHE = {'token': None, 'expire': 0}


def _get_tenant_token():
    now = time.time()
    if TOKEN_CACHE['token'] and now < TOKEN_CACHE['expire'] - 1800:
        return TOKEN_CACHE['token']

    feishu = get_feishu_config()
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": feishu.get('app_id', ''),
        "app_secret": feishu.get('app_secret', '')
    }
    try:
        resp = http_requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get('code') == 0:
            TOKEN_CACHE['token'] = data['tenant_access_token']
            TOKEN_CACHE['expire'] = now + data.get('expire', 7200)
            log.info("Token 获取/刷新成功")
            return TOKEN_CACHE['token']
        else:
            log.error(f"Token 获取失败: {data.get('msg')}")
            return None
    except Exception as e:
        log.error(f"Token 请求异常: {e}")
        return None


def send_text(open_id, text):
    global LARK_CLIENT
    request = CreateMessageRequest.builder() \
        .receive_id_type("open_id") \
        .request_body(
            CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
        ) \
        .build()

    response = LARK_CLIENT.im.v1.message.create(request)
    if not response.success():
        log.error(f"文本消息发送失败: code={response.code}, msg={response.msg}")
        return False
    return True


def send_image(open_id, image_key):
    global LARK_CLIENT
    request = CreateMessageRequest.builder() \
        .receive_id_type("open_id") \
        .request_body(
            CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("image")
                .content(json.dumps({"image_key": image_key}))
                .build()
        ) \
        .build()

    response = LARK_CLIENT.im.v1.message.create(request)
    if not response.success():
        log.error(f"图片消息发送失败: code={response.code}, msg={response.msg}")
        return False
    return True


def upload_image(image_path):
    token = _get_tenant_token()
    if not token:
        log.error("上传图片失败: 无法获取 token")
        return None

    url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}
    files = {
        "image_type": (None, "message"),
        "image": (os.path.basename(image_path), open(image_path, 'rb'), "image/png")
    }
    try:
        resp = http_requests.post(url, headers=headers, files=files, timeout=30)
        data = resp.json()
        if data.get('code') == 0:
            image_key = data['data']['image_key']
            log.info(f"图片上传成功, image_key={image_key}")
            return image_key
        else:
            log.error(f"图片上传失败: code={data.get('code')}, msg={data.get('msg')}")
            return None
    except Exception as e:
        log.error(f"图片上传异常: {e}")
        return None


def _process_command(open_id, text):
    log.info(f"[处理开始] 用户={open_id[:12]}...")
    try:
        results = query_npu_status()
        log.info(f"查询完成: {len(results)} 台服务器")

        image_path = generate_status_image(results)

        image_key = upload_image(image_path)
        if image_key:
            ok = send_image(open_id, image_key)
            if ok:
                log.info("[处理完成] 图片消息发送成功 ✓")
            else:
                send_text(open_id, "⚠️ 图片发送失败，请稍后重试")
        else:
            send_text(open_id, "⚠️ 图片上传失败，请稍后重试")
    except Exception as e:
        log.error(f"[处理异常] {e}")
        try:
            send_text(open_id, f"⚠️ 处理异常: {e}")
        except Exception:
            pass


def _on_message_receive(data: P2ImMessageReceiveV1):
    try:
        event = data.event
        if not event or not event.message or not event.sender:
            return
        sender_id = event.sender.sender_id.open_id if event.sender.sender_id else None
        if not sender_id:
            return
        content = json.loads(event.message.content)
        text = content.get('text', '').strip()
        log.info(f"[收到消息] text=\"{text}\"")
        if text.lower() in ['npu', '/npu', 'npu status', '查看npu']:
            threading.Thread(
                target=_process_command,
                args=(sender_id, text),
                daemon=True
            ).start()
    except Exception as e:
        log.error(f"[消息处理异常] {e}")


def main():
    global LARK_CLIENT

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    feishu = get_feishu_config()
    app_id = feishu.get('app_id', '')
    app_secret = feishu.get('app_secret', '')

    if not app_id or not app_secret:
        log.error("飞书 app_id 或 app_secret 未配置")
        sys.exit(1)

    servers = get_servers()
    if not servers:
        log.error("服务器列表为空")
        sys.exit(1)

    LARK_CLIENT = LarkClient.builder() \
        .app_id(app_id) \
        .app_secret(app_secret) \
        .build()

    token = _get_tenant_token()
    if not token:
        log.error("无法获取 tenant_access_token，请检查 app_id 和 app_secret")
        sys.exit(1)

    event_handler = (
        EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message_receive)
        .build()
    )

    log.info("正在连接飞书 WebSocket...")
    ws = WSClient(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=event_handler
    )
    ws.start()


if __name__ == '__main__':
    main()
