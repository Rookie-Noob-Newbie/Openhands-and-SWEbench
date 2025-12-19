#!/bin/bash

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 配置
DOMAIN="swe.httpbin.test"
HTTPBIN_PORT=18234
CADDY_DIR="/root/httpbin-caddy-setup"
HOSTS_FILE="/etc/hosts"

echo -e "${GREEN}=== 设置 httpbin 和 Caddy 服务 ===${NC}"

# 1. 获取本机 IP
echo -e "${YELLOW}[1/4] 获取本机 IP 地址...${NC}"
# 尝试多种方法获取 IP
if command -v hostname &> /dev/null; then
    # 方法1: 使用 hostname -I
    IP=$(hostname -I | awk '{print $1}')
elif command -v ip &> /dev/null; then
    # 方法2: 使用 ip 命令
    IP=$(ip route get 8.8.8.8 2>/dev/null | grep -oP 'src \K\S+' || ip addr show | grep -oP 'inet \K[\d.]+' | grep -v '127.0.0.1' | head -1)
else
    # 方法3: 使用 ifconfig
    IP=$(ifconfig | grep -oP 'inet \K[\d.]+' | grep -v '127.0.0.1' | head -1)
fi

if [ -z "$IP" ]; then
    IP="127.0.0.1"
    echo -e "${YELLOW}警告: 无法自动获取 IP，使用 127.0.0.1${NC}"
else
    echo -e "${GREEN}本机 IP: $IP${NC}"
fi

# 2. 创建配置目录
echo -e "${YELLOW}[2/4] 创建配置目录...${NC}"
mkdir -p "$CADDY_DIR"
cd "$CADDY_DIR"

# 3. 更新 hosts 文件
echo -e "${YELLOW}[3/4] 更新 hosts 文件...${NC}"
if ! grep -q "$DOMAIN" "$HOSTS_FILE"; then
    echo "$IP $DOMAIN" | sudo tee -a "$HOSTS_FILE" > /dev/null
    echo -e "${GREEN}已添加 $DOMAIN -> $IP 到 hosts 文件${NC}"
else
    # 如果已存在，更新它
    sudo sed -i "s/.*$DOMAIN.*/$IP $DOMAIN/" "$HOSTS_FILE"
    echo -e "${GREEN}已更新 hosts 文件中的 $DOMAIN 映射${NC}"
fi

# 4. 创建 Caddyfile
echo -e "${YELLOW}[4/4] 创建 Caddyfile 配置...${NC}"
cat > "$CADDY_DIR/Caddyfile" <<EOF
# HTTP 服务 - 不重定向到 HTTPS
http://$DOMAIN {
    # 反向代理到 httpbin 服务
    reverse_proxy localhost:$HTTPBIN_PORT {
        header_up Host {host}
        header_up X-Real-IP {remote}
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
    }
    
    # 日志
    log {
        output file /var/log/caddy/httpbin-http.log
    }
}

# HTTPS 服务 - 使用自签名证书
https://$DOMAIN {
    # 启用 TLS 并使用自签名证书
    tls internal
    
    # 反向代理到 httpbin 服务
    reverse_proxy localhost:$HTTPBIN_PORT {
        header_up Host {host}
        header_up X-Real-IP {remote}
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
    }
    
    # 日志
    log {
        output file /var/log/caddy/httpbin-https.log
    }
}
EOF

echo -e "${GREEN}Caddyfile 已创建${NC}"

# 5. 启动 httpbin 服务（使用 Docker）
echo -e "${YELLOW}[5/5] 启动服务...${NC}"

# 检查 httpbin 是否已在运行
if docker ps | grep -q "httpbin"; then
    echo -e "${YELLOW}httpbin 容器已在运行${NC}"
else
    echo -e "${GREEN}启动 httpbin 容器...${NC}"
    docker run -d \
        --name httpbin \
        --restart unless-stopped \
        -p $HTTPBIN_PORT:80 \
        kennethreitz/httpbin || echo -e "${YELLOW}httpbin 容器可能已存在，尝试启动...${NC}"
    docker start httpbin 2>/dev/null || true
fi

# 等待 httpbin 启动
sleep 2

# 检查 Caddy 是否已安装
if ! command -v caddy &> /dev/null; then
    echo -e "${YELLOW}正在安装 Caddy...${NC}"
    sudo apt-get update
    sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
    sudo apt-get update
    sudo apt-get install -y caddy
fi

# 检查 80/443 端口是否被占用
echo -e "${YELLOW}检查端口占用情况...${NC}"
if sudo lsof -i :80 -i :443 2>/dev/null | grep -v "caddy" | grep -q LISTEN; then
    echo -e "${YELLOW}警告: 80 或 443 端口可能被其他服务占用${NC}"
    echo -e "${YELLOW}如果 Caddy 无法启动，请先停止占用端口的服务${NC}"
fi

# 停止可能正在运行的 Caddy
sudo pkill caddy 2>/dev/null || true
sleep 1

# 创建日志目录
sudo mkdir -p /var/log/caddy
sudo chown $(whoami):$(whoami) /var/log/caddy 2>/dev/null || true

# 启动 Caddy（使用 sudo 以绑定特权端口）
echo -e "${GREEN}启动 Caddy 服务...${NC}"
cd "$CADDY_DIR"

# 创建 systemd 服务文件以便更好地管理
if [ ! -f /etc/systemd/system/caddy-httpbin.service ]; then
    sudo tee /etc/systemd/system/caddy-httpbin.service > /dev/null <<EOF
[Unit]
Description=Caddy reverse proxy for httpbin
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$CADDY_DIR
ExecStart=/usr/bin/caddy run --config $CADDY_DIR/Caddyfile --adapter caddyfile
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable caddy-httpbin.service
fi

# 启动 Caddy 服务
sudo systemctl start caddy-httpbin.service || {
    echo -e "${YELLOW}尝试直接运行 Caddy...${NC}"
    sudo caddy run --config Caddyfile --adapter caddyfile &
}

# 等待 Caddy 启动
sleep 3

# 验证服务
echo -e "\n${GREEN}=== 验证服务状态 ===${NC}"
echo -e "检查 httpbin 服务..."
if curl -s http://localhost:$HTTPBIN_PORT/get > /dev/null; then
    echo -e "${GREEN}✓ httpbin 服务运行正常${NC}"
else
    echo -e "${RED}✗ httpbin 服务未响应${NC}"
fi

echo -e "\n检查 Caddy HTTP 服务..."
if curl -s http://$DOMAIN/get > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Caddy HTTP 反向代理工作正常（状态码 200，无重定向）${NC}"
else
    echo -e "${YELLOW}⚠ Caddy HTTP 可能需要更多时间启动，请稍后重试${NC}"
fi

echo -e "\n检查 Caddy HTTPS 服务..."
if curl -s -k https://$DOMAIN/get > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Caddy HTTPS 反向代理工作正常${NC}"
else
    echo -e "${YELLOW}⚠ Caddy HTTPS 可能需要更多时间启动，请稍后重试${NC}"
fi

echo -e "\n${GREEN}=== 设置完成 ===${NC}"
echo -e "域名: ${GREEN}$DOMAIN${NC}"
echo -e "本机 IP: ${GREEN}$IP${NC}"
echo -e "httpbin 端口: ${GREEN}$HTTPBIN_PORT${NC}"
echo -e "Caddy 配置目录: ${GREEN}$CADDY_DIR${NC}"
echo -e "\n访问地址:"
echo -e "  - HTTP:  ${GREEN}http://$DOMAIN/get${NC} (直接返回 200，不重定向)"
echo -e "  - HTTPS: ${GREEN}https://$DOMAIN/get${NC} (自签名证书)"
echo -e "\n注意: 由于使用自签名证书，浏览器会显示安全警告，请选择继续访问。"
echo -e "\nPython 访问示例:"
echo -e "  import requests"
echo -e "  response = requests.get('https://$DOMAIN/get', verify=False)"
echo -e "  # verify=False 会忽略证书验证，状态码仍然是 200"
echo -e "\n测试脚本: python3 /root/test-httpbin-https.py"
echo -e "\n停止服务: sudo /root/stop-httpbin-caddy.sh"
echo -e "查看日志: sudo tail -f /var/log/caddy/httpbin.log"

