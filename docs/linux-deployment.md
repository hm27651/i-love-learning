# Linux Docker Compose 部署

本方案面向 Ubuntu/Debian 内网服务器。Linux 与 Windows 是两个独立实例，不共享 SQLite、不自动同步题库和学习记录；Linux 首次启动会创建空题库。

## 1. 安装 Docker

使用 Docker 官方软件源安装 Engine 与 Compose 插件：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
. /etc/os-release
sudo curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

重新登录后确认 `docker version` 和 `docker compose version` 正常。

## 2. 准备代码与持久化目录

```bash
sudo install -d -o "$USER" -g "$(id -gn)" /opt/i-love-learning
git clone https://github.com/hm27651/i-love-learning.git /opt/i-love-learning
cd /opt/i-love-learning

sudo install -d -m 0750 -o 10001 -g 10001 /srv/i-love-learning/data
sudo install -d -m 0750 -o 10001 -g 10001 /srv/i-love-learning/backups

cp deploy/linux/config.example.env deploy/linux/.env
chmod 600 deploy/linux/.env
```

编辑 `deploy/linux/.env`，将 `BIND_IP` 改成服务器实际的 RFC1918 内网地址。不要填写 `0.0.0.0`、公网地址或 Windows 电脑地址。`DATA_PATH` 必须位于宿主机本地 ext4/xfs 等文件系统，不能放在 NFS/SMB 网络盘。

## 3. 限制内网访问

先放行当前 SSH，再为实际内网网段放行 23456：

```bash
sudo ufw allow OpenSSH
sudo ufw allow from <LAN_CIDR> to any port 23456 proto tcp
sudo ufw enable
sudo ufw status verbose
```

将 `<LAN_CIDR>` 替换为实际值，例如 `192.168.2.0/24`。Compose 只把端口绑定到 `BIND_IP`，这是避免公网暴露的主要边界。Docker 会管理主机防火墙规则；部署后必须分别从允许和不允许的网络验证访问结果。若服务器连接多个可路由内网，应再在上游路由器或 `DOCKER-USER` 链限制来源。

## 4. 构建并启动

```bash
cd /opt/i-love-learning
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml config
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml build --pull
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml up -d
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml ps
```

验证服务、容器用户和空数据库：

```bash
curl --fail "http://<SERVER_LAN_IP>:23456/health"
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml exec -T app id
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml logs --tail=100 app
```

容器用户应为 `uid=10001`，健康接口应返回数据库 `ok` 和 schema `4`。浏览器访问 `http://<SERVER_LAN_IP>:23456/`；初始题目数为零。

## 5. 备份与恢复

在网页进入“数据管理”，点击“立即完整备份”。快照会出现在宿主机：

```text
/srv/i-love-learning/backups/<时间戳>/data
```

需要恢复时：

```bash
cd /opt/i-love-learning
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml stop app
sudo mv /srv/i-love-learning/data "/srv/i-love-learning/data.before-restore-$(date +%Y%m%d-%H%M%S)"
sudo cp -a /srv/i-love-learning/backups/<备份目录>/data /srv/i-love-learning/data
sudo chown -R 10001:10001 /srv/i-love-learning/data
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml start app
curl --fail "http://<SERVER_LAN_IP>:23456/health"
```

确认恢复正确后再删除 `data.before-restore-*`。本方案不配置定时备份；请在重要更新前手动创建快照，并按需把整个备份目录复制到另一台设备。

## 6. 更新与回退

更新前先在网页创建完整备份，然后执行：

```bash
cd /opt/i-love-learning
test -z "$(git status --porcelain)"
git pull --ff-only
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml build --pull
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml up -d --remove-orphans
docker compose --env-file deploy/linux/.env -f deploy/linux/compose.yaml ps
curl --fail "http://<SERVER_LAN_IP>:23456/health"
```

若新版迁移后异常，应同时回退代码和更新前的完整 `data` 快照。不要使用 `docker compose down -v`，不要删除 `/srv/i-love-learning/data`，也不要把 Windows 备份恢复到这个独立 Linux 实例。
