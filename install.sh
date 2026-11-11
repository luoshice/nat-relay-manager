#!/usr/bin/env bash
set -Eeuo pipefail
REPO="${NRM_REPO:-luoshice/nat-relay-manager}"
BRANCH="${NRM_BRANCH:-main}"
APP_DIR="/usr/local/lib/nrm"
log() { printf '\n[NRM] %s\n' "$*"; }
die() { printf '\n[NRM][ERROR] %s\n' "$*" >&2; exit 1; }
[[ "$(id -u)" -eq 0 ]] || die "请使用 root 执行安装程序。"
command -v apt-get >/dev/null 2>&1 || die "当前版本仅支持 Debian / Ubuntu（apt）。"
. /etc/os-release
case "${ID:-}" in debian|ubuntu) ;; *) die "暂不支持当前系统：${PRETTY_NAME:-unknown}。" ;; esac
ARCH="$(uname -m)"
case "$ARCH" in x86_64|aarch64) ;; *) die "暂不支持 CPU 架构：$ARCH。" ;; esac
[[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "NRM_REPO 格式应为 owner/repo。"
export DEBIAN_FRONTEND=noninteractive
log "安装基础依赖……"
apt-get -o Acquire::Retries=3 update
apt-get install -y --no-install-recommends ca-certificates curl openssl python3 tar gzip iproute2
log "添加 sing-box 官方软件源……"
install -d -m 0755 /etc/apt/keyrings
curl -fsSL --retry 3 https://sing-box.app/gpg.key -o /etc/apt/keyrings/sagernet.asc
chmod 0644 /etc/apt/keyrings/sagernet.asc
cat > /etc/apt/sources.list.d/sagernet.sources <<'EOF'
Types: deb
URIs: https://deb.sagernet.org/
Suites: *
Components: *
Enabled: yes
Signed-By: /etc/apt/keyrings/sagernet.asc
EOF
apt-get -o Acquire::Retries=3 update
apt-get install -y --no-install-recommends sing-box
SB_PATH="$(command -v sing-box || true)"
SB_BIN="$(readlink -f "$SB_PATH" 2>/dev/null || printf '%s' "$SB_PATH")"
[[ -n "$SB_BIN" ]] || die "sing-box 安装后未找到可执行文件。"
ln -sfn "$SB_BIN" /usr/local/bin/sing-box
apt-get clean
rm -rf /var/lib/apt/lists/*
log "下载 NRM 项目文件……"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
ARCHIVE="$TMP_DIR/nrm.tar.gz"
curl -fsSL --retry 3 "https://codeload.github.com/${REPO}/tar.gz/refs/heads/${BRANCH}" -o "$ARCHIVE"
mkdir -p "$TMP_DIR/src"
tar -xzf "$ARCHIVE" --strip-components=1 -C "$TMP_DIR/src"
if [[ ! -s "$TMP_DIR/src/nrm.py" ]]; then
  base64 -d "$TMP_DIR/src/nrm.py.gz.b64" | gzip -dc > "$TMP_DIR/src/nrm.py" || die "NRM Python 源码解压失败。"
fi
[[ -s "$TMP_DIR/src/nrm.py" && -s "$TMP_DIR/src/supervisor.sh" ]] || die "下载的项目文件不完整。"
python3 -m py_compile "$TMP_DIR/src/nrm.py" || die "NRM Python 文件语法检查失败。"
install -d -m 0755 "$APP_DIR"
install -m 0755 "$TMP_DIR/src/nrm.py" "$APP_DIR/nrm.py"
install -m 0755 "$TMP_DIR/src/supervisor.sh" "$APP_DIR/supervisor.sh"
install -d -m 0755 /usr/local/bin
cat > /usr/local/bin/nrm <<'EOF'
#!/bin/sh
exec python3 /usr/local/lib/nrm/nrm.py "$@"
EOF
chmod 0755 /usr/local/bin/nrm
install -d -m 0700 /etc/nrm /run/nrm
: > /var/log/nrm.log
chmod 0600 /var/log/nrm.log
if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet sing-box.service; then
    log "检测到已有 sing-box.service 正在运行；NRM 不会自动停用它。若旧服务占用相同监听端口，请先确认后再启动 NRM。"
  fi
  SERVICE_TEMPLATE="$TMP_DIR/src/systemd/nrm.service"
  [[ -s "$SERVICE_TEMPLATE" ]] || die "缺少 systemd 服务模板。"
  log "使用与 singbox-lite 一致的服务启动方式：不设置 User=/Group=，避免容器内 systemd 用户切换失败。"
  install -m 0644 "$SERVICE_TEMPLATE" /etc/systemd/system/nrm.service
  systemctl daemon-reload
  log "已安装 systemd 服务。"
else
  log "未检测到 systemd：容器启动持久性由宿主机/容器编排器负责。"
fi
log "安装完成。"
sing-box version
nrm doctor
if [[ -t 0 ]]; then
  printf '\n现在开始初始化 NAT 入口？[Y/n] '
  read -r answer || true
  if [[ -z "${answer:-}" || "$answer" =~ ^[Yy]$ ]]; then nrm init; else log "稍后运行：nrm init"; fi
else
  log "请运行：nrm init"
fi
