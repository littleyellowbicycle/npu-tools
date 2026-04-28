#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书NPU监控机器人 v17
修复: 1. 去掉状态图标 2. 添加统计校验 3. 重新读取数据机制
新增: 4. --local 本地模式，查询 NPU 状态并输出到 stdout
"""

import os
import sys
import json
import time
import yaml
import re
import argparse          # ★ 新增
import logging
import threading
import concurrent.futures
import requests as http_requests
from PIL import Image, ImageDraw, ImageFont

# 飞书SDK
from lark_oapi import Client, EventDispatcherHandler
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    CreateMessageRequest,
    CreateMessageRequestBody,
)
from lark_oapi.ws import Client as WSClient

# ======================== 日志配置 ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("NPU-Monitor")

# ======================== 全局变量 ========================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.yaml')
IMAGE_PATH = '/tmp/npu_status.png'

FEISHU_APP_ID = ''
FEISHU_APP_SECRET = ''
SERVERS = []

LARK_CLIENT = None
TOKEN_CACHE = {'token': None, 'expire': 0}
EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=20)
MAX_RETRIES = 1  # 最大重试次数


# ======================== 配置加载 ========================
def load_config():
    """加载YAML配置文件"""
    global FEISHU_APP_ID, FEISHU_APP_SECRET, SERVERS

    if not os.path.exists(CONFIG_PATH):
        log.error(f"配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    feishu = config.get('feishu', {})
    FEISHU_APP_ID = feishu.get('app_id', '')
    FEISHU_APP_SECRET = feishu.get('app_secret', '')
    SERVERS = config.get('servers', [])

    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        log.error("飞书 app_id 或 app_secret 未配置")
        sys.exit(1)

    if not SERVERS:
        log.error("服务器列表为空")
        sys.exit(1)

    log.info(f"配置加载成功: {len(SERVERS)} 台服务器")


# ======================== Token 管理 ========================
def get_tenant_token():
    """获取 tenant_access_token（带缓存）"""
    now = time.time()
    if TOKEN_CACHE['token'] and now < TOKEN_CACHE['expire'] - 1800:
        return TOKEN_CACHE['token']

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
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


# ======================== 飞书消息发送 ========================
def send_text_message(open_id, text):
    """发送纯文本消息"""
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


def send_image_message(open_id, image_key):
    """发送图片消息（通过 image_key）"""
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


def upload_image_via_http(image_path):
    """通过 HTTP 直接上传图片到飞书"""
    token = get_tenant_token()
    if not token:
        log.error("上传图片失败: 无法获取 token")
        return None

    url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {
        "Authorization": f"Bearer {token}"
    }
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


# ======================== SSH 查询模块（添加校验逻辑） ========================
def ssh_exec(server, command):
    """SSH 执行命令，返回输出字符串"""
    import paramiko
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=server['host'],
            port=server.get('port', 22),
            username=server.get('username', 'root'),
            password=server.get('password', ''),
            timeout=5,
            look_for_keys=False,
            allow_agent=False
        )
        _, stdout, stderr = client.exec_command(command, timeout=10)
        output = stdout.read().decode('utf-8', errors='replace').strip()
        client.close()
        return output
    except Exception as e:
        return f'Error: {e}'


def parse_npu_output(output):
    """解析 npu-smi info 输出"""
    if not output or output.startswith('Error:'):
        return {'idle': [], 'busy': [], 'error': output or '无输出', 'total': 0}

    lines = output.split('\n')
    idle = set()  # 空闲的 NPU ID
    busy = set()  # 占用的 NPU ID（从进程表）

    in_process_table = False

    for line in lines:
        stripped = line.strip()

        # 检测进程表开始
        if 'NPU' in stripped and 'Chip' in stripped and 'Process id' in stripped:
            in_process_table = True
            continue

        # 检测空闲卡消息
        m = re.search(r'No running processes found in NPU\s+(\d+)', stripped)
        if m:
            npu_id = int(m.group(1))
            idle.add(npu_id)
            continue

        # 在进程表内，解析占用卡
        if in_process_table:
            # 匹配格式: | NPU_ID  Chip_ID  | PID  | Process name | ...
            m = re.search(r'\|\s*(\d+)\s+\d+\s*\|\s*(\d+)', stripped)
            if m:
                npu_id = int(m.group(1))
                pid = int(m.group(2))
                if pid > 0:  # 有实际进程
                    busy.add(npu_id)

    # 计算总卡数
    all_ids = idle | busy
    if all_ids:
        # 假设 NPU ID 从 0 开始且连续，取最大值 + 1
        total_cards = max(all_ids) + 1
    else:
        total_cards = 0

    # 空闲卡 = idle 中不在 busy 中的
    idle = sorted(idle - busy)

    log.debug(f"解析结果: 总卡数={total_cards}, 空闲={idle}, 占用={sorted(busy)}")

    return {
        'idle': idle,
        'busy': sorted(busy),
        'error': None,
        'total': total_cards
    }


def check_server_with_retry(server, max_retries=MAX_RETRIES):
    """查询单台服务器 NPU 状态"""
    host = server['host']  # 保存 host 以便在日志中使用

    for attempt in range(max_retries):
        output = ssh_exec(server, 'npu-smi info')
        result = parse_npu_output(output)
        result['host'] = host  # 添加 host 信息

        if result['error']:
            log.warning(f"服务器 {host} 查询失败 (尝试 {attempt + 1}/{max_retries}): {result['error']}")
            time.sleep(1)
            continue

        log.info(f"服务器 {host} 查询成功: 总卡数={result['total']}, "
                f"空闲={len(result['idle'])}, 占用={len(result['busy'])}")
        return result

    # 重试后仍然失败，返回最后结果（确保 host 存在）
    if 'host' not in result:
        result['host'] = host
    return result


def check_all_servers():
    """并发查询所有服务器（带校验）"""
    futures = {EXECUTOR.submit(check_server_with_retry, s): s for s in SERVERS}
    results = []
    for f in futures:
        server = futures[f]
        try:
            result = f.result(timeout=15)
            results.append(result)
        except Exception as e:
            results.append({
                'host': server['host'],
                'idle': [],
                'busy': [],
                'error': str(e),
                'total': 0
            })
    return results


# ======================== 本地模式 ========================  ★ 新增整个段落
def strip_ansi(text):
    """去除 ANSI 转义序列，只保留纯文本用于宽度计算"""
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def display_width(text):
    """计算字符串的显示宽度（中文字符占2宽度，英文字符占1宽度）"""
    import unicodedata
    width = 0
    for char in text:
        if unicodedata.east_asian_width(char) in ('F', 'W'):
            width += 2  # 全角字符（中文、日文等）
        else:
            width += 1  # 半角字符
    return width


def pad_right(text, width):
    """将文本右填充到指定的显示宽度"""
    current_width = display_width(text)
    padding = width - current_width
    return text + ' ' * max(0, padding)


def local_mode():
    """本地模式：查询所有服务器 NPU 状态并输出到 stdout，然后退出"""
    log.info("=== 本地模式 ===")
    try:
        results = check_all_servers()
        log.info(f"查询完成: {len(results)} 台服务器")
    except Exception as e:
        log.error(f"查询异常: {e}")
        print(f"\n查询失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 分离成功和失败的服务器
    success_results = [r for r in results if not r.get('error')]
    error_results = [r for r in results if r.get('error')]
    err_count = len(error_results)

    # 格式化输出
    idle_all = 0
    busy_all = 0

    print()
    # 固定显示宽度配置
    COL_HOST = 15
    COL_IDLE = 25
    COL_BUSY = 25
    COL_STATUS = 8
    SEP = "   "  # 3个空格分隔符

    # 打印表头
    header_host = pad_right('服务器', COL_HOST)
    header_idle = pad_right('空闲卡', COL_IDLE)
    header_busy = pad_right('占用卡', COL_BUSY)
    header_status = pad_right('状态', COL_STATUS)

    # 计算实际显示宽度
    total_width = display_width(header_host) + len(SEP) + display_width(header_idle) + len(SEP) + display_width(header_busy) + len(SEP) + display_width(header_status)

    print('=' * total_width)
    print(f"  {header_host}{SEP}{header_idle}{SEP}{header_busy}{SEP}{header_status}")
    print('-' * total_width)

    # 只打印成功的服务器
    for r in success_results:
        host = r.get('host', 'unknown')
        parts = host.split('.')
        disp_host = f"192.168.25.{parts[-1]}" if len(parts) >= 4 else host

        ic = len(r.get('idle', []))
        bc = len(r.get('busy', []))
        idle_all += ic
        busy_all += bc

        idle_str = f"{ic}卡 ({fmt_list(r.get('idle', []))})"
        busy_str = f"{bc}卡 ({fmt_list(r.get('busy', []))})"

        if ic > 0:
            status_text = '有空闲'
            status = f'\033[32m{status_text}\033[0m'
        elif bc > 0:
            status_text = '全占用'
            status = f'\033[33m{status_text}\033[0m'
        else:
            status_text = '无卡'
            status = f'\033[90m{status_text}\033[0m'

        # 使用显示宽度填充
        line = f"  {pad_right(disp_host, COL_HOST)}{SEP}{pad_right(idle_str, COL_IDLE)}{SEP}{pad_right(busy_str, COL_BUSY)}{SEP}{strip_ansi(status):<{COL_STATUS}}"
        print(line)

    print('-' * total_width)
    total_idle = f"{idle_all} 卡空闲"
    total_busy = f"{busy_all} 卡占用"
    # 计算总计行的实际宽度来对齐
    space_for_idle = COL_IDLE - display_width('卡空闲') - display_width(str(idle_all)) - 1
    space_for_busy = COL_BUSY - display_width('卡占用') - display_width(str(busy_all)) - 1
    print(f"  {pad_right('总计', COL_HOST)}{SEP}\033[32m{total_idle}\033[0m{' ' * max(0, space_for_idle)}{SEP}\033[33m{total_busy}\033[0m{' ' * max(0, space_for_busy)}")
    print('=' * total_width)

    # 如果有错误服务器，额外输出详情
    if error_results:
        print(f"\n⚠  {err_count} 台服务器查询失败:", file=sys.stderr)
        for r in error_results:
            print(f"   - {r.get('host', 'unknown')}: {r['error']}", file=sys.stderr)
        print(file=sys.stderr)

    # 以退出码反映状态
    # 0 = 全部成功且有空闲  1 = 部分出错  2 = 无空闲卡  3 = 全部失败
    if err_count == len(results):
        sys.exit(3)
    elif idle_all == 0:
        sys.exit(2)
    elif err_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


# ======================== 图片生成模块（去掉图标） ========================
def fmt_list(lst):
    """格式化卡列表"""
    if not lst:
        return '-'
    if len(lst) > 8:
        return f'{",".join(map(str, lst[:3]))}...{",".join(map(str, lst[-2:]))}'
    return ','.join(map(str, lst))


def generate_status_image(results, path=IMAGE_PATH):
    """生成 NPU 状态汇总图片（去掉图标，修复统计）"""
    WIDTH = 760
    PAD = 25
    HDR_H = 50
    ROW_H = 40
    FTR_H = 42
    TITLE_H = 38
    total_rows = len(results)
    total_h = PAD + TITLE_H + HDR_H + total_rows * ROW_H + FTR_H + PAD
    HEIGHT = total_h

    # 颜色
    C_BG = (255, 255, 255)
    C_HDR = (230, 245, 255)
    C_FTR = (230, 245, 255)
    C_ROW1 = (255, 255, 255)
    C_ROW2 = (247, 248, 250)
    C_BORDER = (200, 210, 220)
    C_TEXT = (55, 55, 55)
    C_GREEN = (34, 150, 50)
    C_ORANGE = (220, 110, 15)
    C_RED = (210, 50, 50)
    C_GRAY = (140, 140, 140)

    # 字体
    font = font_b = font_t = None
    font_paths = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 14)
                font_b = ImageFont.truetype(fp, 15)
                font_t = ImageFont.truetype(fp, 20)
                log.info(f"使用字体: {fp}")
                break
            except Exception as e:
                log.warning(f"字体加载失败: {fp} - {e}")
    if not font:
        font = font_b = font_t = ImageFont.load_default()
        log.warning("使用默认字体，可能显示乱码")

    img = Image.new('RGB', (WIDTH, HEIGHT), C_BG)
    d = ImageDraw.Draw(img)

    # 标题
    y = PAD
    d.text((PAD, y), "📊 NPU 空闲状态", fill=C_TEXT, font=font_t)
    y += TITLE_H

    # 表头背景
    d.rectangle([PAD, y, WIDTH - PAD, y + HDR_H], fill=C_HDR, outline=C_BORDER, width=2)
    cols = [
        (PAD + 15, "服务器"),
        (PAD + 210, "空闲卡"),
        (PAD + 400, "占用卡"),
        (PAD + 580, "状态"),
    ]
    for cx, txt in cols:
        d.text((cx, y + 15), txt, fill=C_TEXT, font=font_b)
    y += HDR_H

    # 数据行
    idle_all = 0
    busy_all = 0
    for i, r in enumerate(results):
        bg = C_ROW1 if i % 2 == 0 else C_ROW2
        d.rectangle([PAD, y, WIDTH - PAD, y + ROW_H], fill=bg, outline=C_BORDER)
        host = r['host']
        parts = host.split('.')
        disp_host = f"192.168.25.{parts[-1]}" if len(parts) >= 4 else host
        d.text((PAD + 15, y + 11), disp_host, fill=C_TEXT, font=font)

        if r['error']:
            d.text((PAD + 210, y + 11), "-", fill=C_TEXT, font=font)
            d.text((PAD + 400, y + 11), "-", fill=C_TEXT, font=font)
            d.text((PAD + 580, y + 11), "错误", fill=C_RED, font=font)
        else:
            total = r['total']
            ic = len(r['idle'])
            bc = len(r['busy'])
            if total > 0:
                idle_all += ic
                busy_all += bc
            else:
                idle_all += ic
                busy_all += bc

            # 空闲
            d.text((PAD + 210, y + 11), f"{ic}卡", fill=C_GREEN if ic > 0 else C_GRAY, font=font)
            d.text((PAD + 252, y + 11), f"({fmt_list(r['idle'])})", fill=C_GRAY, font=font)
            # 占用
            d.text((PAD + 400, y + 11), f"{bc}卡", fill=C_ORANGE if bc > 0 else C_GRAY, font=font)
            d.text((PAD + 442, y + 11), f"({fmt_list(r['busy'])})", fill=C_GRAY, font=font)

            # 状态
            if ic > 0:
                status_text = "有空闲"
                status_color = C_GREEN
            elif bc > 0:
                status_text = "全占用"
                status_color = C_ORANGE
            else:
                status_text = "无卡"
                status_color = C_GRAY
            d.text((PAD + 580, y + 11), status_text, fill=status_color, font=font)
        y += ROW_H

    # 底部汇总
    d.rectangle([PAD, y, WIDTH - PAD, y + FTR_H], fill=C_FTR, outline=C_BORDER, width=2)
    d.text((PAD + 15, y + 12), "总计", fill=C_TEXT, font=font_b)
    d.text((PAD + 210, y + 12), f"{idle_all} 卡空闲", fill=C_GREEN, font=font_b)
    d.text((PAD + 400, y + 12), f"{busy_all} 卡占用", fill=C_ORANGE, font=font_b)

    # 保存
    img.save(path, format='PNG')
    fsize = os.path.getsize(path)
    log.info(f"图片已生成: {path} ({fsize} bytes, PNG)")
    return path


# ======================== 业务处理 ========================
def process_command(open_id, text):
    """后台线程：查询 NPU → 生成图片 → 上传 → 发送"""
    log.info(f"[处理开始] 用户={open_id[:12]}...")
    try:
        # 1. 查询所有服务器（带校验）
        results = check_all_servers()
        log.info(f"查询完成: {len(results)} 台服务器")

        # 2. 生成图片
        image_path = generate_status_image(results)

        # 3. 上传图片
        image_key = upload_image_via_http(image_path)
        if image_key:
            # 4. 发送图片消息
            ok = send_image_message(open_id, image_key)
            if ok:
                log.info("[处理完成] 图片消息发送成功 ✓")
            else:
                send_text_message(open_id, "⚠️ 图片发送失败，请稍后重试")
        else:
            send_text_message(open_id, "⚠️ 图片上传失败，请稍后重试")
    except Exception as e:
        log.error(f"[处理异常] {e}")
        try:
            send_text_message(open_id, f"⚠️ 处理异常: {e}")
        except Exception:
            pass


# ======================== WebSocket 事件处理 ========================
def on_message_receive(data: P2ImMessageReceiveV1):
    """飞书消息接收回调"""
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
                target=process_command,
                args=(sender_id, text),
                daemon=True
            ).start()
    except Exception as e:
        log.error(f"[消息处理异常] {e}")


# ======================== 主函数 ========================
def main():
    global LARK_CLIENT

    # ★ 新增：解析命令行参数
    parser = argparse.ArgumentParser(
        description='飞书NPU监控机器人',
        epilog='示例:\n'
               '  python npuv16.py             # 启动飞书 WebSocket 长连接模式\n'
               '  python npuv16.py --local      # 本地模式：查询并输出到 stdout',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--local',
        action='store_true',
        default=False,
        help='本地模式：查询所有服务器 NPU 状态并输出到 stdout，不连接飞书',
    )
    args = parser.parse_args()

    # 1. 加载配置
    load_config()

    # ★ 新增：如果是本地模式，查询后输出并退出
    if args.local:
        local_mode()
        return  # local_mode 内部会调用 sys.exit

    # 2. 初始化 lark_oapi 客户端（以下为原有逻辑，本地模式不会执行）
    LARK_CLIENT = Client.builder() \
        .app_id(FEISHU_APP_ID) \
        .app_secret(FEISHU_APP_SECRET) \
        .build()

    # 3. 预热 token
    token = get_tenant_token()
    if not token:
        log.error("无法获取 tenant_access_token，请检查 app_id 和 app_secret")
        sys.exit(1)

    # 4. 注册事件处理器
    event_handler = (
        EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )

    # 5. 启动 WebSocket 长连接
    log.info("正在连接飞书 WebSocket...")
    ws = WSClient(
        app_id=FEISHU_APP_ID,
        app_secret=FEISHU_APP_SECRET,
        event_handler=event_handler
    )
    ws.start()


if __name__ == '__main__':
    main()


