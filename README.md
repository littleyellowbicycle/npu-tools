<div align="center">

# NPU Tools

多节点 NPU 集群管理工具 — 查询状态 · 部署脚本 · 自动同步

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org/) [![Paramiko](https://img.shields.io/badge/Paramiko-3.0+-2F6F9F?style=flat-square)](https://www.paramiko.org/) [![MCP](https://img.shields.io/badge/MCP-1.0+-FF6B35?style=flat-square)](https://modelcontextprotocol.io/) [![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

[快速开始](#-快速开始) · [核心特性](#-核心特性) · [架构](#-架构) · [使用方式](#-使用方式) · [配置说明](#-配置说明)

</div>

---

## 理念

> 管理集群不应该比使用集群更累。

跑大模型推理服务通常要使用多个 NPU 节点，每次查状态要逐台 SSH、部署脚本要逐台上传、改了代码要逐台同步。NPU Tools 把这些重复操作变成一句话的事——通过 MCP 让 AI 直接操作集群，通过飞书让手机也能查状态，通过文件监控让开发时自动同步。

---

## ✨ 核心特性

### 🔍 NPU 状态查询

SSH 并发查询多台服务器 `npu-smi info`，智能解析输出，精确区分空闲 / 占用 / 异常卡。终端彩色表格、飞书状态图片、MCP 文本结果三种输出格式。

### 🚀 一键智能部署

一条命令完成全流程：自动选择空闲节点 → 创建 Docker 容器 → 同步脚本 → 在容器中启动。支持宿主机 / Docker 两种执行模式，按节点配置自动切换。

### 🔄 脚本同步与自动监控

单文件同步、目录批量同步、文件变更自动监控——修改本地代码，远程节点实时更新。

### 🐳 Docker 容器管理

在 `config.yaml` 中预配置 Docker 创建命令，一键创建容器、查看容器状态。脚本执行自动通过 `docker exec` 在容器内运行，文件通过 volume 挂载同步。

### 🤖 MCP 协议集成

通过 MCP (Model Context Protocol) 暴露所有能力，AI 工具（如 Trae、Claude Code、Codex）可直接调用查询状态、部署脚本、管理节点。

### 💬 飞书机器人

WebSocket 长连接，用户在飞书发送 `npu` 即可获取 NPU 状态图片推送。

### 📦 轻量单文件

只需 `npu_query.py` + `config.yaml` 两个文件，`pip install paramiko pyyaml` 即可查询 NPU 状态。

---

## 🏗 架构

```
┌──────────────────────────────────────────────────────────┐
│                    Channel Layer (通道层)                  │
│                                                           │
│   MCP Server          Feishu Bot          CLI             │
│   AI 工具调用           飞书消息触发          终端命令行       │
│   (stdio)             (WebSocket)         (argparse)      │
└────────┬──────────────────┬──────────────────┬───────────┘
         │                  │                  │
┌────────┴──────────────────┴──────────────────┴───────────┐
│                   Service Layer (服务层)                    │
│                                                           │
│   ┌─────────────────┐    ┌─────────────────────┐         │
│   │   NPU Service   │    │       Develop        │         │
│   │  查询 · 解析 ·   │    │  部署 · 同步 · 监控  │         │
│   │  图片生成        │    │  Docker · 进程管理   │         │
│   └────────┬────────┘    └────────┬────────────┘         │
└────────────┼──────────────────────┼──────────────────────┘
             │                      │
┌────────────┼──────────────────────┼──────────────────────┐
│            ┼    Driver Layer (驱动层)    ┼                │
│                                                           │
│   ┌────────▼────────────────────────┐                    │
│   │          SSH Driver             │                    │
│   │     exec_command · upload_file  │                    │
│   └─────────────────────────────────┘                    │
└──────────────────────────────────────────────────────────┘
         │
    ┌────▼────┐
    │ config  │  统一配置，三层共用
    │ .yaml   │
    └─────────┘
```

### 层级职责

| 层级 | 目录 | 职责 |
|------|------|------|
| **Channel** | `channel/` | 接收不同来源的请求，编排调用服务层，返回结果 |
| **Service** | `services/` | 纯业务逻辑，不关心谁调用、怎么传输 |
| **Driver** | `driver/` | 驱动外部系统（SSH），不关心业务 |
| **Config** | `config.py` + `config.yaml` | 统一配置加载，三层共用 |

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- 可 SSH 连接的 NPU 服务器

### 最简方式 — 2 个文件查询状态

```bash
# 只需两个文件：npu_query.py + config.yaml
pip install paramiko pyyaml
python npu_query.py
```

输出示例：

```
==================================================================================
  服务器            空闲卡                      占用卡                      状态
----------------------------------------------------------------------------------
  192.168.25.212    7卡 (1,2,3,4,5,6,7)         1卡 (0)                     有空闲
  192.168.25.218    2卡 (4,5)                   6卡 (0,1,2,3,6,7)           有空闲
----------------------------------------------------------------------------------
  总计              9 卡空闲                    7 卡占用
==================================================================================
```

### 完整安装 — 全部功能

```bash
git clone <repo-url> npu-tools
cd npu-tools
pip install -r requirements.txt
```

---

## 📖 使用方式

### 1. 终端命令（CLI）

CLI 使用 argparse 子命令，支持 15 个操作：

```bash
# 查询 NPU 状态（默认命令）
python channel/cli.py
python channel/cli.py query

# 一键智能部署（选节点 + Docker + 同步 + 启动）
python channel/cli.py deploy train.py
python channel/cli.py deploy train.py --count 2 --min-idle 4 --args "--epochs 100"
python channel/cli.py deploy train.py --hosts 192.168.25.212 192.168.25.213
python channel/cli.py deploy train.py --container my-container

# 自动选择空闲节点部署（不含 Docker）
python channel/cli.py auto train.py --count 4

# 后台启动脚本
python channel/cli.py launch train.py
python channel/cli.py launch train.py --hosts 192.168.25.212 192.168.25.213 --args "--epochs 10"

# 同步脚本到节点
python channel/cli.py sync ./train.py

# 批量同步目录
python channel/cli.py sync-dir --dir ./scripts

# 查看运行日志
python channel/cli.py log /opt/npu-tools/logs/train_192-168-25-212_20260611_143000.log
python channel/cli.py log /opt/npu-tools/logs/train_192-168-25-212_20260611_143000.log --lines 100

# 列出日志文件
python channel/cli.py logs
python channel/cli.py logs --keyword train

# 列出运行中的进程
python channel/cli.py ps
python channel/cli.py ps --keyword train

# 终止进程
python channel/cli.py stop 12345

# Docker 容器管理
python channel/cli.py docker
python channel/cli.py containers

# 启动/停止文件监控
python channel/cli.py watch
python channel/cli.py watch-stop

# 列出配置的服务器
python channel/cli.py servers
```

也支持通过兼容入口：

```bash
python npu_status_query.py --local deploy train.py
python npu_status_query.py --local query
python npu_status_query.py --local ps
```

退出码说明（query 命令）：

| 退出码 | 含义 |
|--------|------|
| 0 | 全部成功且有空闲卡 |
| 1 | 部分服务器查询失败 |
| 2 | 无空闲卡 |
| 3 | 全部服务器查询失败 |

### 2. MCP 工具调用（AI 集成）

NPU Tools 通过 MCP 协议暴露所有能力，支持任何兼容 MCP 的 AI 编码工具。

#### Trae

在 Trae 的 MCP 设置中添加：

```json
{
  "mcpServers": {
    "npu-tools": {
      "command": "python",
      "args": ["d:\\project\\npu-tools\\channel\\mcp_server.py"],
      "cwd": "d:\\project\\npu-tools"
    }
  }
}
```

#### Claude Code

命令行添加：

```bash
claude mcp add npu-tools -s user -- python d:/project/npu-tools/channel/mcp_server.py
```

或编辑 `~/.claude.json`：

```json
{
  "mcpServers": {
    "npu-tools": {
      "command": "python",
      "args": ["d:/project/npu-tools/channel/mcp_server.py"],
      "cwd": "d:/project/npu-tools"
    }
  }
}
```

#### Codex CLI

命令行添加：

```bash
codex mcp add npu-tools -- python d:/project/npu-tools/channel/mcp_server.py
```

或编辑 `~/.codex/config.toml`：

```toml
[mcp_servers.npu-tools]
command = "python"
args = ["d:/project/npu-tools/channel/mcp_server.py"]
cwd = "d:/project/npu-tools"
```

#### OpenCode

编辑 `opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "npu-tools": {
      "type": "local",
      "command": ["python", "d:/project/npu-tools/channel/mcp_server.py"],
      "enabled": true,
      "environment": {}
    }
  }
}
```

> 💡 **提示**：请将路径替换为你的实际安装路径。Windows 用户注意使用 `\\` 或 `/` 作为路径分隔符。

#### 可用工具

| 工具 | 说明 |
|------|------|
| `smart_deploy` | **【推荐】** 一键智能部署：选节点 → Docker → 同步 → 启动（支持 `hosts` 指定节点、`container` 覆盖容器名） |
| `auto_deploy` | 自动选择空闲节点部署（不含 Docker） |
| `query_npu_status` | 查询 NPU 状态（空闲/占用卡） |
| `launch_script` | 后台启动脚本（nohup），返回 PID 和日志路径 |
| `get_script_log` | 查看脚本运行日志（tail） |
| `list_logs` | 列出远程节点上的日志文件，支持按脚本名筛选 |
| `list_processes` | 列出远程节点上运行的 Python 进程 |
| `stop_script` | 终止指定 PID 的进程 |
| `sync_script` | 同步单个脚本到节点 |
| `sync_directory` | 批量同步目录下所有脚本 |
| `start_file_watcher` | 启动文件变更自动同步 |
| `stop_file_watcher` | 停止文件监控 |
| `setup_docker` | 创建/启动 Docker 容器 |
| `list_containers` | 列出 Docker 容器状态 |
| `list_servers` | 列出所有配置的服务器节点 |

#### 使用示例

配置完成后，直接在对话中使用：

**智能部署（最常用）：**

- "帮我找 4 台空闲机器部署 train.py" → `smart_deploy` 一键完成
- "找 2 台至少 4 卡空闲的机器跑 train.py --epochs 100" → `smart_deploy(count=2, min_idle_cards=4)`
- "在 212 和 213 上部署 train.py" → `smart_deploy(hosts=["192.168.25.212", "192.168.25.213"])`
- "在 docker my-container 中执行 train.py" → `smart_deploy(container="my-container")`
- "在 212 的 docker test 中跑 train.py" → `smart_deploy(hosts=["192.168.25.212"], container="test")`

**查询与监控：**

- "查询 NPU 状态" → 查询所有节点
- "查看 train.py 的日志" → 查看运行输出
- "列出所有日志文件" → 查看历史日志
- "列出所有运行中的 Python 进程" → 查看进程状态

**操作管理：**

- "停止 PID 12345" → 终止进程
- "同步 train.py 到服务器" → 上传脚本
- "创建 Docker 容器" → `setup_docker`
- "启动文件监控" → 自动同步变更

### 3. 飞书机器人

```bash
python channel/feishu.py
# 或
python npu_status_query.py
```

启动后连接飞书 WebSocket，用户发送 `npu`、`/npu`、`npu status` 或 `查看npu` 即可触发查询，机器人自动返回 NPU 状态图片。

飞书应用配置：

1. 前往 [飞书开放平台](https://open.feishu.cn/app) 创建应用
2. 开启 **机器人** 能力
3. 在 **凭证与基础信息** 中获取 `app_id` 和 `app_secret`
4. 配置消息权限：`im:message`（发送消息）

---

## ⚙️ 配置说明

编辑 `config.yaml`：

```yaml
# Docker 默认配置（所有 docker: true 的节点共享）
docker_default:
  container: "sglang-0610"             # 容器名
  image: "61036f139a18"                # 镜像 ID 或名称
  workdir: "/root/ott13"               # 容器内工作目录（挂载点）
  shm_size: "512g"                     # 共享内存
  privileged: true                     # 特权模式
  network_host: true                   # 主机网络
  hostname: "ubuntu-docker"            # 容器主机名
  entrypoint: "bash"                   # 入口点
  volumes:                             # 挂载列表
    - "/mnt:/mnt"
    - "/home:/home"
    - "/data:/data"
    - "/root/ott13:/root/ott13"
    - "/usr/local/Ascend/driver:/usr/local/Ascend/driver"
  devices:                             # 设备映射
    - "/dev/davinci0:/dev/davinci0"
    - "/dev/davinci_manager:/dev/davinci_manager"
    - "/dev/hisi_hdc:/dev/hisi_hdc"

# 服务器列表
servers:
  - host: "192.168.25.212"
    port: 22
    username: "root"
    password: "YOUR_SERVER_PASSWORD"
    docker: true                       # 使用 docker_default 配置

  - host: "192.168.25.213"
    port: 22
    username: "root"
    password: "YOUR_SERVER_PASSWORD"
    docker:                            # 覆盖部分默认值
      container: "custom-name"
      image: "my-image:v2"

  - host: "192.168.25.216"
    port: 22
    username: "root"
    password: "YOUR_SERVER_PASSWORD"
    # 无 docker = 直接在宿主机执行

# 飞书机器人配置（仅飞书模式需要）
feishu:
  app_id: "YOUR_FEISHU_APP_ID"
  app_secret: "YOUR_FEISHU_APP_SECRET"

# 脚本部署配置
script_deploy:
  remote_dir: "/opt/npu-tools"       # 远程部署目录（宿主机路径）
  # deploy_nodes:                     # 可选：默认部署节点，不配置则使用 servers 中所有节点
  #   - "192.168.25.212"
  watch_dir: "."                      # 本地监控目录
```

### Docker 配置说明

| 写法 | 说明 |
|------|------|
| `docker: true` | 使用 `docker_default` 全部配置 |
| `docker: { container: "xxx" }` | 继承 `docker_default`，覆盖指定字段 |
| 无 `docker` 字段 | 不使用 Docker，直接在宿主机执行 |

| 字段 | 说明 |
|------|------|
| `container` | 容器名，用于 `docker exec` 执行命令 |
| `image` | 镜像 ID 或名称，如 `61036f139a18` 或 `ascend-train:latest` |
| `workdir` | 容器内工作目录，需与 `volumes` 中的挂载对应 |
| `volumes` | 宿主机到容器的目录映射列表 |
| `devices` | NPU 设备映射列表 |
| `shm_size` | 共享内存大小（训练任务建议 512g） |
| `privileged` | 特权模式（NPU 访问通常需要） |
| `network_host` | 使用主机网络 |
| `entrypoint` | 容器入口点 |
| `create_cmd` | 完整 `docker run` 命令（优先级最高，设置后忽略其他字段） |

**关键**：`volumes` 中必须将宿主机 `remote_dir`（如 `/opt/npu-tools`）映射到容器 `workdir`（如 `/root/ott13`），这样同步到宿主机的文件才能在容器内访问。

---

## 📂 项目结构

```
npu-tools/
├── npu_query.py                 # 轻量单文件入口（零依赖分层）
├── npu_status_query.py          # 向后兼容入口
├── config.py                    # 统一配置加载
├── config.yaml                  # 配置文件
│
├── channel/                     # Channel 层 — 通信通道
│   ├── mcp_server.py            #   MCP 通道（AI ↔ 我们）
│   ├── feishu.py                #   飞书通道（用户 ↔ 我们）
│   └── cli.py                   #   终端通道（命令行 ↔ 我们）
│
├── services/                    # Service 层 — 业务逻辑
│   ├── npu_service.py           #   NPU 查询、解析、图片生成
│   └── develop.py               #   部署、同步、监控、Docker、进程管理
│
├── driver/                      # Driver 层 — 驱动外部系统
│   └── ssh_driver.py            #   SSH/SFTP 驱动
│
└── requirements.txt
```

### 依赖方向

```
Channel → Service → Driver → Config
  │          │         │        │
  │          │         │        └── config.yaml
  │          │         └── ssh_driver.py (SSH/SFTP)
  │          └── npu_service / develop (业务逻辑)
  └── mcp_server / feishu / cli (通信通道)
```

严格单向依赖，不允许反向调用。

---

## 🧭 典型工作流

### 一键智能部署（最常用）

```
对话: "帮我找4台空闲机器部署 train.py"
  → MCP smart_deploy
  → 自动: 查询状态 → 选4台空闲节点 → 创建Docker → 同步脚本 → 启动脚本
  → 返回: 选中节点、PID、日志路径
  → 对话: "查看日志" → MCP get_script_log
```

### 指定节点和容器部署

```
对话: "在 212 和 213 的 docker sglang 中执行 train.py"
  → MCP smart_deploy(hosts=["192.168.25.212", "192.168.25.213"], container="sglang")
  → 跳过自动选择，直接在指定节点的指定容器中部署

对话: "在 docker my-test 中跑 train.py"
  → MCP smart_deploy(container="my-test")
  → 自动选择空闲节点，但覆盖容器名为 my-test
```

### 精细控制部署

```
对话: "找2台至少4卡空闲的机器跑 train.py --epochs 100"
  → MCP smart_deploy(count=2, min_idle_cards=4, args="--epochs 100")
  → 自动选择满足条件的节点，完成全流程
```

### 查看集群状态

```
对话: "查询 NPU 状态"
  → MCP query_npu_status
  → 返回各节点空闲/占用信息
```

### 开发时自动同步

```
对话: "启动文件监控"
  → MCP start_file_watcher
  → 修改本地代码 → 自动同步到节点
  → 对话: "运行 train.py" → MCP launch_script
  → 对话: "查看日志"     → MCP get_script_log
  → 对话: "停止监控"     → MCP stop_file_watcher
```

---

## 📜 License

[MIT License](LICENSE)

**如果这个项目对你有帮助，请给个 ⭐ Star！**
