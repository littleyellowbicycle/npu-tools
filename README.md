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

Output example:

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

### Feishu Bot Mode

```bash
python3 npu_status_query.py
```

Connects to Feishu WebSocket and responds to messages with NPU status.