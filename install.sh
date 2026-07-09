#!/usr/bin/env bash
# Qcby VxCode 一键管理脚本（安装 / 升级 / 卸载 / 运维）
# Docker Hub: https://hub.docker.com/r/qcby/qcby-vxcode
#
# 常用：
#   bash install.sh install [端口] [版本]
#   bash install.sh update  [端口] [版本]
#   bash install.sh uninstall [--purge]
#   bash install.sh status|address|logs|restart|reset-password|help
#
# 管道：
#   curl -fsSL https://cdn.jsdelivr.net/gh/Qcby/QcbyScript@code/install.sh | bash                         # 打开交互菜单
#   curl -fsSL https://cdn.jsdelivr.net/gh/Qcby/QcbyScript@code/install.sh | bash -s -- install 8110 latest # 明确指定安装
#   curl -fsSL https://cdn.jsdelivr.net/gh/Qcby/QcbyScript@code/install.sh | bash -s -- update 8110 1.0.4
#
# 环境变量：
#   IMAGE_TAG=latest         指定镜像版本
#   HOST_PORT=8110          指定宿主机端口
#   YES=1                   非交互确认继续
#   DELETE_VOLUMES=1        卸载时删除数据卷
#   USE_MIRROR=1            配置 Docker registry mirror（默认不改 Docker 配置）
#   MIRROR_URL=https://docker.1ms.run
#   REDIS_PASSWORD=xxx      为 Redis 设置密码并传给业务容器
#   NO_AUTO_INSTALL_DOCKER=1 禁止自动安装 Docker

set -Eeuo pipefail

# ==================== 项目配置 ====================
APP_NAME="qcby-vxcode"
REDIS_NAME="qcby-redis"
NETWORK_NAME="qcby-net"
APP_VOLUME="qcby-vxcode-data"
REDIS_VOLUME="qcby-redis-data"
IMAGE_REPO="${IMAGE_REPO:-qcby/qcby-vxcode}"
REDIS_IMAGE="${REDIS_IMAGE:-redis:7-alpine}"
CONTAINER_PORT="8110"
DEFAULT_HOST_PORT="8110"
DEFAULT_IMAGE_TAG="${IMAGE_TAG:-latest}"
MIRROR_URL="${MIRROR_URL:-https://docker.1ms.run}"
PROJECT_URL="https://hub.docker.com/r/qcby/qcby-vxcode"
SCRIPT_URL="https://cdn.jsdelivr.net/gh/Qcby/QcbyScript@code/install.sh"
ADMIN_AUTH_FILE="/app/data/admin_auth.json"
# ==================================================

SCRIPT_NAME="${0##*/}"
ACTION=""
DOCKER_BIN="docker"
DOCKER_PREFIX=()

red() { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
blue() { printf '\033[34m%s\033[0m\n' "$*"; }
info() { blue "[信息] $*"; }
warn() { yellow "[提示] $*"; }
err() { red "[错误] $*" >&2; }

# curl ... | bash 时 stdin 被管道占用，但终端通常仍可通过 /dev/tty 交互。
# 所以交互判断不能只看 stdin，避免用户一执行命令就直接自动安装 Docker。
can_prompt() { [ -t 1 ] && [ -r /dev/tty ]; }
is_tty() { can_prompt; }
is_root() { [ "${EUID:-$(id -u)}" -eq 0 ]; }
have() { command -v "$1" >/dev/null 2>&1; }

prompt_read() {
  # 用法：prompt_read var_name
  # 在管道执行脚本时从 /dev/tty 读取用户输入；无 TTY 时回退到 stdin。
  local __var="$1"
  if [ -r /dev/tty ]; then
    IFS= read -r "$__var" </dev/tty
  else
    IFS= read -r "$__var"
  fi
}

run_docker() {
  "${DOCKER_PREFIX[@]}" "$DOCKER_BIN" "$@"
}

usage() {
  cat <<EOF
=== Qcby VxCode 管理脚本 ===
项目地址：$PROJECT_URL
脚本地址：$SCRIPT_URL
镜像：$IMAGE_REPO:<版本>（latest 和版本号标签均支持多架构 AMD64 / ARM64）

用法：
  $SCRIPT_NAME install [端口] [版本]       安装，默认端口 8110，默认版本 latest
  $SCRIPT_NAME update  [端口] [版本]       升级/更新，只重建业务容器并保留数据
  $SCRIPT_NAME upgrade [端口] [版本]       update 的别名
  $SCRIPT_NAME uninstall [--purge]         卸载容器；加 --purge 同时删除数据卷
  $SCRIPT_NAME status                      查看容器状态
  $SCRIPT_NAME address                    查看访问地址和后台管理地址
  $SCRIPT_NAME logs [app|redis]            查看日志，默认 app
  $SCRIPT_NAME restart [app|redis|all]     重启服务，默认 all
  $SCRIPT_NAME reset-password              删除后台鉴权文件并重启，重新初始化管理员
  $SCRIPT_NAME help                        显示帮助

一键命令：
  curl -fsSL $SCRIPT_URL | bash                         # 打开交互菜单
  curl -fsSL $SCRIPT_URL | bash -s -- install 8110 latest # 明确指定安装
  curl -fsSL $SCRIPT_URL | bash -s -- update 8110 1.0.4

示例：
  bash $SCRIPT_NAME install
  bash $SCRIPT_NAME install 8110 latest
  bash $SCRIPT_NAME update 9000 1.0.4
  bash $SCRIPT_NAME uninstall --purge

环境变量：
  HOST_PORT=8110 IMAGE_TAG=latest YES=1 USE_MIRROR=0 DELETE_VOLUMES=0 REDIS_PASSWORD=xxx
EOF
}

legal_notice() {
  cat <<'EOF'

============================================================
【使用须知 / 免责声明】
1. 本项目及脚本仅供学习、研究、测试与合法授权场景使用。
2. 请在下载、安装、测试后 24 小时内删除；继续使用视为你已取得合法授权。
3. 禁止将本项目用于违法违规、侵犯他人权益、破坏平台规则或未授权用途。
4. 因下载、安装、运行、传播、改造或使用本项目产生的一切后果由使用者自行承担。
============================================================
EOF
}

confirm_notice() {
  legal_notice
  if [ "${YES:-0}" = "1" ]; then
    info "检测到 YES=1，跳过交互确认。"
    return 0
  fi
  if is_tty; then
    printf "请输入 y 确认已阅读并继续，其他任意输入退出 [y/N]: "
    prompt_read answer
    case "$answer" in
      y|Y|yes|YES) return 0 ;;
      *) err "已取消。"; exit 1 ;;
    esac
  else
    warn "未检测到可交互终端，已展示使用须知；继续执行即代表同意。"
  fi
}


scan_common_flags() {
  # 支持把 --yes / -y / --purge 放在任意位置，例如：install 8110 latest --yes
  local arg
  for arg in "${REMAINING_ARGS[@]:-}"; do
    case "$arg" in
      --yes|-y) YES=1 ;;
      --purge|--delete-volumes|-p) DELETE_VOLUMES=1 ;;
    esac
  done
}
parse_action() {

  local first="${1:-}"
  case "$first" in
    install|1) ACTION="install"; shift || true ;;
    update|upgrade|2) ACTION="update"; shift || true ;;
    uninstall|remove|3) ACTION="uninstall"; shift || true ;;
    status|ps) ACTION="status"; shift || true ;;
    address|url|admin|addr) ACTION="address"; shift || true ;;
    logs|log) ACTION="logs"; shift || true ;;
    restart) ACTION="restart"; shift || true ;;
    reset-password|reset_password|reset) ACTION="reset-password"; shift || true ;;
    help|-h|--help) ACTION="help"; shift || true ;;
    "")
      if is_tty; then
        echo "请选择操作："
        echo "  1) 安装"
        echo "  2) 升级/更新"
        echo "  3) 卸载"
        echo "  4) 查看状态"
        echo "  5) 查看日志"
        echo "  6) 重置后台密码"
        echo "  7) 查看管理地址"
        echo "  8) 退出脚本"
        printf "输入数字 [1-8]: "
        prompt_read choice
        case "$choice" in
          1) ACTION="install" ;;
          2) ACTION="update" ;;
          3) ACTION="uninstall" ;;
          4) ACTION="status" ;;
          5) ACTION="logs" ;;
          6) ACTION="reset-password" ;;
          7) ACTION="address" ;;
          8) echo "已退出脚本。"; exit 0 ;;
          *) err "无效选择。"; exit 1 ;;
        esac
      else
        ACTION="install"
        warn "未检测到可交互终端且未指定操作，默认执行安装。"
      fi
      ;;
    *) err "未知操作：$first"; usage; exit 1 ;;
  esac
  REMAINING_ARGS=("$@")
}

valid_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

valid_tag() {
  [[ "$1" =~ ^[A-Za-z0-9_.-]+$ ]]
}

positional_arg() {
  # 从 REMAINING_ARGS 里取第 N 个非选项参数，避免 install --yes 被当作端口。
  local want="$1"
  local count=0
  local arg
  for arg in "${REMAINING_ARGS[@]:-}"; do
    case "$arg" in
      --yes|-y|--purge|--delete-volumes|-p) continue ;;
    esac
    count=$((count + 1))
    if [ "$count" -eq "$want" ]; then
      printf '%s' "$arg"
      return 0
    fi
  done
  return 0
}

choose_host_port() {
  local arg_port="${1:-}"
  local existing_port=""
  if have docker || [ ${#DOCKER_PREFIX[@]} -gt 0 ]; then
    existing_port="$(run_docker inspect --format='{{range $p, $conf := .NetworkSettings.Ports}}{{if eq $p "8110/tcp"}}{{(index $conf 0).HostPort}}{{end}}{{end}}' "$APP_NAME" 2>/dev/null || true)"
  fi

  if [ -n "$arg_port" ]; then
    HOST_PORT="$arg_port"
  elif [ -n "${HOST_PORT:-}" ]; then
    HOST_PORT="$HOST_PORT"
  elif [ "$ACTION" = "update" ] && [ -n "$existing_port" ]; then
    HOST_PORT="$existing_port"
    info "检测到现有映射端口：$HOST_PORT"
  elif is_tty; then
    printf "请输入访问端口（默认 %s）: " "$DEFAULT_HOST_PORT"
    prompt_read input_port
    HOST_PORT="${input_port:-$DEFAULT_HOST_PORT}"
  else
    HOST_PORT="$DEFAULT_HOST_PORT"
    info "未检测到可交互终端，使用默认端口 $HOST_PORT。"
  fi

  if ! valid_port "$HOST_PORT"; then
    err "端口无效：$HOST_PORT（应为 1-65535）"
    exit 1
  fi
}

choose_image_tag() {
  local arg_tag="${1:-}"
  IMAGE_TAG="${arg_tag:-$DEFAULT_IMAGE_TAG}"
  if ! valid_tag "$IMAGE_TAG"; then
    err "镜像版本标签无效：$IMAGE_TAG"
    exit 1
  fi
}

select_docker_prefix() {
  if ! have docker; then
    return 1
  fi
  DOCKER_BIN="$(command -v docker)"
  DOCKER_PREFIX=()
  if "$DOCKER_BIN" info >/dev/null 2>&1; then
    return 0
  fi
  if have sudo && sudo -n "$DOCKER_BIN" info >/dev/null 2>&1; then
    DOCKER_PREFIX=(sudo -n)
    return 0
  fi
  return 2
}

start_docker_service() {
  if have systemctl; then
    systemctl enable docker >/dev/null 2>&1 || true
    systemctl start docker >/dev/null 2>&1 || true
  elif have service; then
    service docker start >/dev/null 2>&1 || true
  fi
}

run_root() {
  if is_root; then
    "$@"
  elif have sudo; then
    sudo "$@"
  else
    return 127
  fi
}

install_docker_with_script_url() {
  local url="$1"
  local tmp=""
  if ! have curl; then
    return 1
  fi
  tmp="$(mktemp)"
  info "尝试通过安装脚本安装 Docker：$url"
  if ! curl -fsSL --connect-timeout 15 --retry 2 --retry-delay 2 "$url" -o "$tmp"; then
    rm -f "$tmp"
    warn "下载安装脚本失败：$url"
    return 1
  fi
  if run_root sh "$tmp"; then
    rm -f "$tmp"
    return 0
  fi
  rm -f "$tmp"
  warn "安装脚本执行失败：$url"
  return 1
}

install_docker_with_get_script() {
  local urls=()
  local url=""
  [ -n "${DOCKER_INSTALL_URL:-}" ] && urls+=("$DOCKER_INSTALL_URL")
  urls+=(
    "https://get.docker.com"
    "https://raw.githubusercontent.com/docker/docker-install/master/install.sh"
    "https://cdn.jsdelivr.net/gh/docker/docker-install@master/install.sh"
  )
  for url in "${urls[@]}"; do
    install_docker_with_script_url "$url" && return 0
  done
  return 1
}

install_docker_with_package_manager() {
  if ! (is_root || have sudo); then
    return 1
  fi

  if have apt-get; then
    info "尝试通过 apt 安装 Docker（docker.io）。"
    run_root apt-get update || return 1
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io ca-certificates curl || return 1
    return 0
  fi

  if have dnf; then
    info "尝试通过 dnf 安装 Docker。"
    run_root dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin && return 0
    run_root dnf install -y moby-engine docker-cli containerd || return 1
    return 0
  fi

  if have yum; then
    info "尝试通过 yum 安装 Docker。"
    run_root yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin && return 0
    run_root yum install -y docker || return 1
    return 0
  fi

  if have apk; then
    info "尝试通过 apk 安装 Docker。"
    run_root apk add --no-cache docker docker-cli containerd || return 1
    return 0
  fi

  if have pacman; then
    info "尝试通过 pacman 安装 Docker。"
    run_root pacman -Sy --noconfirm docker || return 1
    return 0
  fi

  if have zypper; then
    info "尝试通过 zypper 安装 Docker。"
    run_root zypper --non-interactive install docker || return 1
    return 0
  fi

  return 1
}

ensure_docker() {
  if select_docker_prefix; then
    info "Docker 已就绪。"
    return 0
  fi

  if have docker; then
    warn "Docker 已安装但当前用户无法访问或 Docker daemon 未启动，尝试启动服务。"
    if is_root || have sudo; then
      start_docker_service
    fi
    if select_docker_prefix; then
      info "Docker 已启动。"
      return 0
    fi
    err "无法连接 Docker。请确认 Docker daemon 正在运行，或将当前用户加入 docker 组。"
    exit 1
  fi

  if [ "${NO_AUTO_INSTALL_DOCKER:-0}" = "1" ]; then
    err "Docker 未安装，且 NO_AUTO_INSTALL_DOCKER=1。"
    exit 1
  fi

  if ! (is_root || have sudo); then
    err "Docker 未安装；当前不是 root 且没有 sudo，无法自动安装。"
    exit 1
  fi

  warn "Docker 未安装，开始自动安装。"
  warn "若 get.docker.com 在当前网络不可达，脚本会自动尝试 GitHub/jsDelivr 安装脚本和系统包管理器。"

  if install_docker_with_get_script; then
    info "Docker 安装脚本执行完成。"
  else
    warn "所有 Docker 官方安装脚本下载或执行失败，改用系统包管理器兜底安装。"
    if ! install_docker_with_package_manager; then
      err "Docker 自动安装失败。可手动执行：apt-get update && apt-get install -y docker.io"
      exit 1
    fi
  fi

  start_docker_service
  sleep 2

  if ! select_docker_prefix; then
    # 部分系统第一次启动较慢，再尝试一次。
    start_docker_service
    sleep 3
  fi

  if ! select_docker_prefix; then
    err "Docker 安装后仍不可用，请手动检查 Docker 服务：systemctl status docker 或 service docker status"
    exit 1
  fi
  green "Docker 安装完成并已启动。"
}

configure_docker_mirror() {
  local use_mirror="${USE_MIRROR:-}"
  if [ -z "$use_mirror" ] && is_tty; then
    printf "是否配置 Docker registry mirror：%s？默认 y [Y/n]: " "$MIRROR_URL"
    prompt_read answer
    case "$answer" in n|N|no|NO) use_mirror=0 ;; *) use_mirror=1 ;; esac
  elif [ -z "$use_mirror" ]; then
    use_mirror=1
    info "未检测到可交互终端，默认配置 Docker registry mirror：$MIRROR_URL。可用 USE_MIRROR=0 关闭。"
  fi
  use_mirror="${use_mirror:-1}"

  if [ "$use_mirror" != "1" ]; then
    info "未修改 Docker registry mirror。需要时可用 USE_MIRROR=1 启用。"
    return 0
  fi

  local daemon_file="/etc/docker/daemon.json"
  if ! is_root; then
    if ! have sudo; then
      warn "配置镜像加速需要 root/sudo，已跳过。"
      return 0
    fi
  fi

  info "正在配置 Docker registry mirror：$MIRROR_URL"
  local tmp
  tmp="$(mktemp)"

  if [ -f "$daemon_file" ]; then
    if have jq; then
      jq --arg mirror "$MIRROR_URL" '."registry-mirrors" = ((."registry-mirrors" // []) + [$mirror] | unique)' "$daemon_file" > "$tmp"
    else
      warn "$daemon_file 已存在但未安装 jq；为避免破坏原配置，跳过自动修改。"
      rm -f "$tmp"
      return 0
    fi
  else
    cat > "$tmp" <<EOF
{
  "registry-mirrors": ["$MIRROR_URL"]
}
EOF
  fi

  if is_root; then
    mkdir -p /etc/docker
    [ -f "$daemon_file" ] && cp "$daemon_file" "$daemon_file.bak.$(date +%Y%m%d%H%M%S)" || true
    mv "$tmp" "$daemon_file"
    start_docker_service
  else
    sudo mkdir -p /etc/docker
    sudo test -f "$daemon_file" && sudo cp "$daemon_file" "$daemon_file.bak.$(date +%Y%m%d%H%M%S)" || true
    sudo mv "$tmp" "$daemon_file"
    sudo systemctl restart docker >/dev/null 2>&1 || sudo service docker restart >/dev/null 2>&1 || true
  fi
  sleep 2
  select_docker_prefix || true
  green "Docker registry mirror 配置完成。"
}

pull_images() {
  info "拉取业务镜像：$IMAGE_REPO:$IMAGE_TAG"
  run_docker pull "$IMAGE_REPO:$IMAGE_TAG"
  info "拉取 Redis 镜像：$REDIS_IMAGE"
  run_docker pull "$REDIS_IMAGE"
}

container_exists() {
  run_docker ps -a --format '{{.Names}}' | grep -Fxq "$1"
}

container_running() {
  run_docker ps --format '{{.Names}}' | grep -Fxq "$1"
}

ensure_network_and_volumes() {
  run_docker network create "$NETWORK_NAME" >/dev/null 2>&1 || true
  run_docker volume create "$APP_VOLUME" >/dev/null
  run_docker volume create "$REDIS_VOLUME" >/dev/null
}

build_common_mount_args() {
  COMMON_MOUNT_ARGS=(-v "$APP_VOLUME:/app/data")
  if [ -d /sys ]; then
    COMMON_MOUNT_ARGS+=(-v "/sys:/host-sys:ro")
  else
    warn "宿主机 /sys 不存在，已跳过该只读挂载。"
  fi
  if [ -f /etc/machine-id ]; then
    COMMON_MOUNT_ARGS+=(-v "/etc/machine-id:/host-etc/machine-id:ro")
  else
    warn "宿主机 /etc/machine-id 不存在，已跳过该只读挂载。"
  fi
}

start_redis() {
  local redis_args=(redis-server --appendonly yes)
  if [ -n "${REDIS_PASSWORD:-}" ]; then
    redis_args+=(--requirepass "$REDIS_PASSWORD")
  fi

  if container_exists "$REDIS_NAME"; then
    if container_running "$REDIS_NAME"; then
      info "Redis 容器已运行。"
    else
      info "启动已有 Redis 容器。"
      run_docker start "$REDIS_NAME" >/dev/null
    fi
    run_docker network connect "$NETWORK_NAME" "$REDIS_NAME" >/dev/null 2>&1 || true
    return 0
  fi

  info "启动 Redis 容器：$REDIS_NAME"
  run_docker run -d \
    --name "$REDIS_NAME" \
    --network "$NETWORK_NAME" \
    -v "$REDIS_VOLUME:/data" \
    --restart always \
    "$REDIS_IMAGE" "${redis_args[@]}" >/dev/null
}

remove_app_container() {
  if container_exists "$APP_NAME"; then
    info "删除旧业务容器：$APP_NAME"
    run_docker rm -f "$APP_NAME" >/dev/null || true
  fi
}

start_app() {
  build_common_mount_args
  local redis_addr="$REDIS_NAME:6379"
  local env_args=(-e "REDIS_ADDR=$redis_addr")
  if [ -n "${REDIS_PASSWORD:-}" ]; then
    env_args+=(-e "REDIS_PASSWORD=$REDIS_PASSWORD")
  fi

  info "启动业务容器：$APP_NAME"
  run_docker run -d \
    --name "$APP_NAME" \
    --network "$NETWORK_NAME" \
    -p "$HOST_PORT:$CONTAINER_PORT" \
    "${COMMON_MOUNT_ARGS[@]}" \
    "${env_args[@]}" \
    --restart always \
    "$IMAGE_REPO:$IMAGE_TAG" >/dev/null
}

get_public_ip() {
  local ip=""
  local url=""
  if have curl; then
    for url in \
      "https://api.ipify.org" \
      "https://ifconfig.me/ip" \
      "https://icanhazip.com" \
      "https://ident.me"; do
      ip="$(curl -fsS --max-time 5 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
      if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        printf '%s' "$ip"
        return 0
      fi
    done
  fi
  return 1
}

get_local_ip() {
  local ip=""
  if have hostname; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  if [ -z "$ip" ] && have ip; then
    ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}' || true)"
  fi
  printf '%s' "${ip:-服务器IP}"
}

print_addresses() {
  local port="$1"
  local public_ip=""
  local local_ip=""
  local_ip="$(get_local_ip)"
  public_ip="$(get_public_ip || true)"

  echo "本机/内网地址：http://$local_ip:$port/"
  echo "本机/内网后台：http://$local_ip:$port/admin/"
  if [ -n "$public_ip" ]; then
    echo "公网访问地址：http://$public_ip:$port/"
    echo "公网后台地址：http://$public_ip:$port/admin/"
    echo "公网安全码后台：http://$public_ip:$port/admin/你的安全码"
  else
    warn "未能自动获取公网 IP；如果服务器有公网 IP，请手动使用：http://公网IP:$port/"
  fi
}

show_success() {
  local title="$1"
  echo ""
  echo "======================================"
  green "✅ $title"
  print_addresses "$HOST_PORT"
  echo "首次访问请设置管理员账号、密码和安全码。"
  echo "项目地址：$PROJECT_URL"
  echo "脚本地址：$SCRIPT_URL"
  echo "常用命令："
  echo "  docker logs -f $APP_NAME"
  echo "  docker restart $APP_NAME"
  echo "  docker exec $APP_NAME sh -c 'rm -f $ADMIN_AUTH_FILE' && docker restart $APP_NAME"
  echo "======================================"
  run_docker ps --filter "name=qcby-"
}

install_app() {
  choose_host_port "$(positional_arg 1)"
  choose_image_tag "$(positional_arg 2)"
  info "访问端口：$HOST_PORT"
  info "镜像版本：$IMAGE_TAG"
  confirm_notice
  ensure_docker
  configure_docker_mirror
  pull_images
  ensure_network_and_volumes

  remove_app_container
  if container_exists "$REDIS_NAME"; then
    warn "检测到已有 Redis 容器，将复用并保留数据。"
  fi
  start_redis
  start_app
  sleep 3
  show_success "安装完成"
}

update_app() {
  choose_host_port "$(positional_arg 1)"
  choose_image_tag "$(positional_arg 2)"
  info "访问端口：$HOST_PORT"
  info "镜像版本：$IMAGE_TAG"
  confirm_notice
  ensure_docker
  configure_docker_mirror
  pull_images
  ensure_network_and_volumes
  start_redis
  remove_app_container
  start_app
  sleep 3
  show_success "升级完成"
}

uninstall_app() {
  confirm_notice
  ensure_docker
  local purge="${DELETE_VOLUMES:-0}"
  for arg in "${REMAINING_ARGS[@]:-}"; do
    case "$arg" in
      --purge|--delete-volumes|-p) purge=1 ;;
      -y|--yes) YES=1 ;;
      *) warn "忽略未知卸载参数：$arg" ;;
    esac
  done
  if [ "$purge" != "1" ] && is_tty; then
    printf "是否同时删除数据卷（会清空后台配置/鉴权数据和 Redis 数据）？默认 n [y/N]: "
    prompt_read answer
    case "$answer" in y|Y|yes|YES) purge=1 ;; esac
  fi

  info "停止并删除容器。"
  run_docker rm -f "$APP_NAME" >/dev/null 2>&1 && green "已删除 $APP_NAME" || warn "$APP_NAME 不存在"
  run_docker rm -f "$REDIS_NAME" >/dev/null 2>&1 && green "已删除 $REDIS_NAME" || warn "$REDIS_NAME 不存在"

  if [ "$purge" = "1" ]; then
    warn "正在删除数据卷。"
    run_docker volume rm "$APP_VOLUME" >/dev/null 2>&1 && green "已删除 $APP_VOLUME" || warn "$APP_VOLUME 不存在或正在被占用"
    run_docker volume rm "$REDIS_VOLUME" >/dev/null 2>&1 && green "已删除 $REDIS_VOLUME" || warn "$REDIS_VOLUME 不存在或正在被占用"
  else
    info "数据卷已保留。如需手动删除：docker volume rm $APP_VOLUME $REDIS_VOLUME"
  fi
  green "✅ 卸载完成。"
}

status_app() {
  ensure_docker
  run_docker ps -a --filter "name=qcby-"
  echo ""
  info "数据卷："
  run_docker volume ls --filter "name=qcby-"
  echo ""
  info "网络："
  run_docker network ls --filter "name=$NETWORK_NAME"
}

show_address() {
  # 只查看已安装服务，不自动安装 Docker。
  if ! select_docker_prefix; then
    err "未检测到可用 Docker，无法读取已安装服务地址。"
    echo "如果尚未安装，请先选择 1) 安装。"
    exit 1
  fi

  if ! container_exists "$APP_NAME"; then
    err "未检测到已安装的业务容器：$APP_NAME"
    echo "如果尚未安装，请先选择 1) 安装。"
    exit 1
  fi

  local port=""
  port="$(run_docker inspect --format='{{(index (index .NetworkSettings.Ports "8110/tcp") 0).HostPort}}' "$APP_NAME" 2>/dev/null || true)"
  if [ -z "$port" ] || [ "$port" = "<no value>" ]; then
    port="$DEFAULT_HOST_PORT"
    warn "未能从容器读取端口映射，使用默认端口 $port 显示。"
  fi

  echo ""
  echo "======================================"
  green "✅ 管理地址"
  print_addresses "$port"
  echo "如果你已经设置过安全码，请优先使用上方 /admin/你的安全码 地址。"
  echo "容器状态："
  run_docker ps --filter "name=$APP_NAME"
  echo "======================================"
}

logs_app() {
  ensure_docker
  local target="${REMAINING_ARGS[0]:-app}"
  case "$target" in
    app|vxcode|qcby-vxcode) run_docker logs -f --tail=200 "$APP_NAME" ;;
    redis|qcby-redis) run_docker logs -f --tail=200 "$REDIS_NAME" ;;
    *) err "未知日志目标：$target（可选 app|redis）"; exit 1 ;;
  esac
}

restart_app() {
  ensure_docker
  local target="${REMAINING_ARGS[0]:-all}"
  case "$target" in
    app|vxcode|qcby-vxcode) run_docker restart "$APP_NAME" ;;
    redis|qcby-redis) run_docker restart "$REDIS_NAME" ;;
    all) run_docker restart "$REDIS_NAME" "$APP_NAME" ;;
    *) err "未知重启目标：$target（可选 app|redis|all）"; exit 1 ;;
  esac
  green "✅ 重启完成。"
}

reset_password() {
  confirm_notice
  ensure_docker
  if ! container_exists "$APP_NAME"; then
    err "业务容器不存在：$APP_NAME"
    exit 1
  fi
  run_docker exec "$APP_NAME" sh -c "rm -f '$ADMIN_AUTH_FILE'"
  run_docker restart "$APP_NAME" >/dev/null
  green "✅ 后台鉴权文件已删除，请重新访问 /admin/ 初始化管理员。"
}

main() {
  echo "=== Qcby VxCode 管理脚本 ==="
  parse_action "$@"
  scan_common_flags
  case "$ACTION" in
    install) install_app ;;
    update) update_app ;;
    uninstall) uninstall_app ;;
    status) status_app ;;
    address) show_address ;;
    logs) logs_app ;;
    restart) restart_app ;;
    reset-password) reset_password ;;
    help) legal_notice; usage ;;
    *) err "未知操作：$ACTION"; usage; exit 1 ;;
  esac
}

main "$@"


