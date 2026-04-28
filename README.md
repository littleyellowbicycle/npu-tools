# NPU Status Query

Query NPU (Neural Processing Unit) status across multiple servers via SSH.

## Configuration

Edit `config.yaml`:

```yaml
servers:
  - host: "192.168.25.212"
    port: 22
    username: "root"
    password: "your_password"

feishu:
  app_id: "your_app_id"
  app_secret: "your_app_secret"
```

## Usage

```bash
python3 npu_status_query.py --local
```

This queries all configured servers and outputs NPU status to stdout.

## Mode

- `--local`: Query NPU status and print to stdout (no Feishu integration)