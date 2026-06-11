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


def get_docker_default():
    config = load_config()
    return config.get('docker_default', None)


def get_docker_config(server):
    if server.get('_docker_override'):
        return server['_docker_override']

    docker = server.get('docker', None)
    if docker is None:
        return None

    default = get_docker_default()
    if docker is True:
        return dict(default) if default else None

    if isinstance(docker, dict):
        if default:
            merged = dict(default)
            merged.update(docker)
            for key in ('volumes', 'devices'):
                if key in docker:
                    merged[key] = docker[key]
            return merged
        return docker

    return None


def build_docker_create_cmd(server):
    docker = get_docker_config(server)
    if not docker:
        return None

    if docker.get('create_cmd'):
        return docker['create_cmd']

    container = docker.get('container', '')
    image = docker.get('image', '')
    if not container or not image:
        return None

    parts = ['docker run -itd']

    if docker.get('shm_size'):
        parts.append(f"--shm-size={docker['shm_size']}")
    if docker.get('privileged'):
        parts.append('--privileged=true')
    if docker.get('network_host'):
        parts.append('--net=host')
    if docker.get('hostname'):
        parts.append(f"--hostname={docker['hostname']}")

    parts.append(f'--name {container}')

    for v in docker.get('volumes', []):
        parts.append(f'-v {v}')

    for d in docker.get('devices', []):
        parts.append(f'--device={d}')

    entrypoint = docker.get('entrypoint')
    if entrypoint:
        parts.append(f'--entrypoint={entrypoint}')

    parts.append(image)

    return ' '.join(parts)


def filter_servers(hosts=None):
    servers = get_servers()
    if not hosts:
        return servers
    return [s for s in servers if s['host'] in hosts]
