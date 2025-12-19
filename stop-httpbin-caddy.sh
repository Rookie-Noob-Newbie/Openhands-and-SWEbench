#!/bin/bash

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}停止 httpbin 和 Caddy 服务...${NC}"

# 停止 Caddy 服务
if systemctl is-active --quiet caddy-httpbin.service 2>/dev/null; then
    echo -e "${GREEN}停止 Caddy systemd 服务...${NC}"
    sudo systemctl stop caddy-httpbin.service
    sudo systemctl disable caddy-httpbin.service
else
    echo -e "${GREEN}停止 Caddy 进程...${NC}"
    sudo pkill caddy 2>/dev/null || true
fi

# 停止 httpbin 容器
if docker ps | grep -q "httpbin"; then
    echo -e "${GREEN}停止 httpbin 容器...${NC}"
    docker stop httpbin
fi

echo -e "${GREEN}服务已停止${NC}"
echo -e "如需完全清理，运行: docker rm httpbin"

