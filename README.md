# QcbyScript

青龙脚本仓库。当前 README 只介绍需要微信小程序 `code` 的脚本，也就是 `CodeScript/` 目录；`RegularScript/` 目录为普通 token / cookie / 账号密码脚本，不在本文展开。

## 目录说明

```text
CodeScript/      需要 wx_server_url / mywc 获取小程序 code 的脚本
RegularScript/   不需要 code 服务的普通脚本
```

## 一、Code 服务安装

Code 服务使用 Docker 镜像 [`qcby/qcby-vxcode`](https://hub.docker.com/r/qcby/qcby-vxcode)。镜像默认服务端口为 `8110`，安装完成后访问：

```text
http://服务器IP:8110/
```

首次打开会进入初始化页面，先设置管理员账号、密码和安全码。

### 终端一键安装脚本

```bash
curl -fsSL https://cdn.jsdelivr.net/gh/Qcby/QcbyScript@code/install.sh | bash
```

### 方式 1：docker run 安装

```bash
docker pull qcby/qcby-vxcode:latest

docker network create qcby-net 2>/dev/null

docker volume create qcby-vxcode-data
docker volume create qcby-redis-data

docker run -d \
  --name qcby-redis \
  --network qcby-net \
  -v qcby-redis-data:/data \
  --restart always \
  redis:7-alpine redis-server --appendonly yes

docker run -d \
  --name qcby-vxcode \
  --network qcby-net \
  -p 8110:8110 \
  -v qcby-vxcode-data:/app/data \
  -v /sys:/host-sys:ro \
  -v /etc/machine-id:/host-etc/machine-id:ro \
  -e REDIS_ADDR=qcby-redis:6379 \
  --restart always \
  qcby/qcby-vxcode:latest
```

### 方式 2：docker compose 安装

创建 `docker-compose.yml`：

```yaml
services:
  redis:
    image: redis:7-alpine
    container_name: qcby-redis
    command: redis-server --appendonly yes
    volumes:
      - qcby-redis-data:/data
    restart: always

  qcby-vxcode:
    image: qcby/qcby-vxcode:latest
    container_name: qcby-vxcode
    depends_on:
      - redis
    ports:
      - "8110:8110"
    volumes:
      - qcby-vxcode-data:/app/data
      - /sys:/host-sys:ro
      - /etc/machine-id:/host-etc/machine-id:ro
    environment:
      REDIS_ADDR: redis:6379
    restart: always

volumes:
  qcby-vxcode-data:
  qcby-redis-data:
```

启动：

```bash
docker compose up -d
```

### 后台入口

首次初始化：

```text
http://服务器IP:8110/admin/
```

设置安全码后，后续使用：

```text
http://服务器IP:8110/admin/你的安全码
```

### `/mywc` 接口格式

所有 CodeScript 脚本统一通过 `/mywc` 获取小程序 code：

```text
GET http://服务器IP:8110/mywc?wxid=你的wxid&appId=小程序AppID
Header: auth=你的wxid
```

青龙环境变量统一配置：

```bash
wx_server_url="http://服务器IP:8110"
# 或
WX_SERVER_URL="http://服务器IP:8110"
```


## 二、青龙拉库命令

在青龙面板「订阅管理」中添加订阅，或在容器终端执行：

```bash
ql repo https://github.com/Qcby/QcbyScript.git "" "" "" "main"
```

只拉取 `CodeScript` 目录可使用：

```bash
ql repo https://github.com/Qcby/QcbyScript.git "CodeScript" "" "" "main"
```

只拉取 `RegularScript` 目录可使用：

```bash
ql repo https://github.com/Qcby/QcbyScript.git "RegularScript" "" "" "main"
```

## 三、CodeScript 脚本列表

| 脚本 | 功能 | AppID | 账号变量 | 青龙命令 |
|---|---|---|---|---|
| `wps.js` | WPS 签到任务 | `wx2f333d84a103825d` | `wps_wxid` 或 `WPS_WXID` | `node wps.js` |
| `优智云家.py` | 优智云家签到 | `wxa61f98248d20178b` | `yzyj_wxid` 或 `YZYJ_WXID` | `python3 优智云家.py` |
| `可口可乐.js` | 可口可乐签到 | `wxa5811e0426a94686` | `coke_wxid` 或 `COKE_WXID` | `node 可口可乐.js` |
| `拼多多果园.js` | 拼多多果园 | `wx32540bd863b27570` | `pdd_wxid` 或 `PDD_WXID` | `node 拼多多果园.js` |
| `拼多多果园.py` | 拼多多果园 | `wx32540bd863b27570` | `pdd_wxid` 或 `PDD_WXID` | `python3 拼多多果园.py` |
| `提现免费券.py` | 微信免费提现券 | `wxdb3c0e388702f785` | `txmfq_wxid` 或 `TXMFQ_WXID` | `python3 提现免费券.py` |
| `美的会员.js` | 美的会员签到 | `wx49a622805968d156` | `midea_wxid` 或 `MIDEA_WXID` | `node 美的会员.js` |
| `腾讯地图.js` | 腾讯地图 | `wx7643d5f831302ab0` | `txdt_wxid` 或 `TXDT_WXID` | `node 腾讯地图.js` |
| `途虎养车.py` | 途虎养车签到 | `wx27d20205249c56a3` | `tuhu_wxid` 或 `TUHU_WXID` | `python3 途虎养车.py` |
| `铛铛一下.py` | 铛铛一下 | `wxe378d2d7636c180e` | `dd1x_wxid` 或 `DD1X_WXID` | `python3 铛铛一下.py` |
| `顺丰.py` | 顺丰速运自动任务 | `wxd4185d00bf7e08ac` | `sf_wxid` 或 `SF_WXID` | `python3 顺丰.py` |

账号变量多账号均支持：

```text
&、英文逗号、中文逗号、换行
```

示例：

```bash
wx_server_url="http://服务器IP:8110"
pdd_wxid="wxid_a&wxid_b"
txdt_wxid="wxid_a,wxid_b"
```

## 四、推送说明

### Python 脚本

Python 脚本使用同目录 `SendNotify.py`：

```text
CodeScript/SendNotify.py
```

常用推送变量任选一种：

```bash
QYWX_KEY="企业微信机器人key"
PUSH_PLUS_TOKEN="PushPlus token"
PUSH_KEY="Server酱 key"
DD_BOT_TOKEN="钉钉机器人 token"
DD_BOT_SECRET="钉钉机器人 secret"
FSKEY="飞书机器人 key"
```

### JavaScript 脚本

JS 脚本内置企业微信机器人聚合推送，推荐配置：

```bash
QYWX_KEY="企业微信机器人key"
```

## 五、Code 服务常用命令

```bash
# 查看容器
docker ps

# 查看业务日志
docker logs -f qcby-vxcode

# 查看 Redis 日志
docker logs -f qcby-redis

# 重启业务容器
docker restart qcby-vxcode

# 重启 Redis
docker restart qcby-redis
```

忘记后台密码时：

```bash
docker exec qcby-vxcode sh -c 'rm -f /app/data/admin_auth.json' && docker restart qcby-vxcode
```

## 来源

Code 服务安装说明来自 Docker Hub：

[https://hub.docker.com/r/qcby/qcby-vxcode](https://hub.docker.com/r/qcby/qcby-vxcode)

