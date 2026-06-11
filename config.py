#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.yaml')

_config_cache = None


def load_config(force_reload=False):
    global _config_cache
    if _config_cache and not force_reload:
        return _config_cache

    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}")

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        _config_cache = yaml.safe_load(f)

    return _config_cache


def get_servers():
    config = load_config()
    return config.get('servers', [])


def get_feishu_config():
    config = load_config()
    return config.get('feishu', {})


def get_deploy_config():
    config = load_config()
    return config.get('script_deploy', {})


def get_deploy_nodes():
    deploy = get_deploy_config()
    return deploy.get('deploy_nodes', [])


def get_remote_dir():
    deploy = get_deploy_config()
    return deploy.get('remote_dir', '/opt/npu-tools')


def get_watch_dir():
    deploy = get_deploy_config()
    return deploy.get('watch_dir', SCRIPT_DIR)


def get_docker_config(server):
    return server.get('docker', None)


def filter_servers(hosts=None):
    servers = get_servers()
    if not hosts:
        return servers
    return [s for s in servers if s['host'] in hosts]
