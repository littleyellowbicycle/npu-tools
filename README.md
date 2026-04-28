# NPU Status Query

Query NPU (Neural Processing Unit) status across multiple servers via SSH.

## Configuration

Edit `config.yaml`:

```yaml
servers:
  - host: "192.168.25.212"
    port: 22
    username: "root"
    password: "YOUR_SERVER_PASSWORD"

feishu:
  app_id: "YOUR_FEISHU_APP_ID"
  app_secret: "YOUR_FEISHU_APP_SECRET"
```

## Feishu Configuration

To enable Feishu bot integration:

1. Create a Feishu app at [Feishu Open Platform](https://open.feishu.cn/app)
2. Enable **Bot** capability
3. Get `app_id` and `app_secret` from **Credentials & Basic Info**
4. Configure message permissions:
   - `im:message` - Send messages
5. Deploy the bot with WebSocket mode

Without Feishu config, the script runs in `--local` mode only.

## Usage

### Local Mode (stdout output)

```bash
python3 npu_status_query.py --local
```

Queries all configured servers and outputs NPU status to stdout.

### Feishu Bot Mode

```bash
python3 npu_status_query.py
```

Connects to Feishu WebSocket and responds to messages with NPU status.