#!/usr/bin/env python3
"""NAT Relay Manager: TCP-only VLESS/REALITY relay management for sing-box."""
from __future__ import annotations

import argparse
import base64
import copy
import getpass
import ipaddress
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "0.3.0"
APP = "NAT Relay Manager"
BASE = Path(os.environ.get("NRM_BASE", "/etc/nrm"))
STATE_FILE = BASE / "state.json"
CONFIG_FILE = BASE / "config.json"
BACKUP_DIR = Path("/root/nrm-backups")
EXPORT_DIR = Path("/root/nrm-export")
LOG_FILE = Path("/var/log/nrm.log")
PID_DIR = Path("/run/nrm")
SUPERVISOR = Path("/usr/local/lib/nrm/supervisor.sh")
SYSTEMD_UNIT = Path("/etc/systemd/system/nrm.service")
MAX_UPSTREAMS = 5
MIN_SING_BOX = (1, 14, 0)


class NRMError(Exception):
    pass


def is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def require_root() -> None:
    if not is_root():
        raise NRMError("请使用 root 运行：sudo nrm ...")


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run(cmd: list[str], *, check: bool = True, capture: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise NRMError(f"命令执行失败：{' '.join(cmd)}\n{detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise NRMError(f"命令超时：{' '.join(cmd)}") from exc


def atomic_json(path: Path, data: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def migrate_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("schema_version") != 1:
        raise NRMError("状态文件版本不受支持，请先备份并升级 NRM。")
    state.setdefault("listen_address", "0.0.0.0")
    state.setdefault("handshake_server", state.get("server_name", "www.microsoft.com"))
    state.setdefault("handshake_port", 443)
    direct_user = state.setdefault("direct_user", {})
    direct_user.setdefault("display_name", "NAT-Direct")
    for item in state.get("upstreams", []):
        item.setdefault("source_server_name", item.get("server_name", ""))
        item.setdefault("server_name_override", False)
        item.setdefault("display_name", f"NAT-{item['name']}")
    mappings = state.setdefault("port_mappings", [])
    if not isinstance(mappings, list):
        raise NRMError("端口映射记录格式错误，请先备份配置。")
    if not any(isinstance(x, dict) and x.get("name") == "nrm-proxy" for x in mappings):
        mappings.insert(0, {"name":"nrm-proxy","protocol":"TCP","external_port":int(state["public_port"]),"internal_port":int(state["listen_port"]),"note":"NRM VLESS + REALITY 入站"})
    return state


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        raise NRMError("尚未初始化。请先运行：nrm init")
    try:
        with STATE_FILE.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise NRMError(f"无法读取状态文件：{exc}") from exc
    return migrate_state(state)

def validate_port(value: str | int, label: str = "端口") -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise NRMError(f"{label}必须是 1–65535 的整数。") from exc
    if not 1 <= port <= 65535:
        raise NRMError(f"{label}必须在 1–65535 之间。")
    return port


def normalize_address(value: str) -> str:
    value = value.strip()
    if not value or any(c.isspace() for c in value) or "/" in value or "@" in value:
        raise NRMError("公网地址格式不正确。请填写 IPv4、IPv6 或域名，不要带协议和端口。")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        if len(value) > 253 or not re.fullmatch(r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*", value):
            raise NRMError("公网地址不是有效的 IP 或域名。")
        return value.lower()


def validate_domain(value: str, label: str = "域名") -> str:
    value = value.strip().rstrip(".")
    if not value or "://" in value or "/" in value or ":" in value or any(c.isspace() for c in value):
        raise NRMError(f"{label}格式不正确。请只填写域名，不要带协议、端口或路径。")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if len(value) > 253 or not re.fullmatch(r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*", value):
            raise NRMError(f"{label}格式不正确。")
    return value.lower()


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"[-_]{2,}", "-", value).strip("-_")
    if not value:
        raise NRMError("节点名称不能为空，且至少包含一个英文字母或数字。")
    return value[:32]


def validate_display_name(value: str) -> str:
    value = str(value).strip()
    if not value:
        raise NRMError("节点显示名称不能为空。")
    if len(value) > 64:
        raise NRMError("节点显示名称最多 64 个字符。")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise NRMError("节点显示名称不能包含控制字符或换行。")
    return value


def node_display_name(state: dict[str, Any], node: str | dict[str, Any]) -> str:
    if node == "direct":
        return state.get("direct_user", {}).get("display_name") or "NAT-Direct"
    if isinstance(node, dict):
        return node.get("display_name") or f"NAT-{node['name']}"
    raise NRMError("无法识别节点。")


def set_node_display_name(state: dict[str, Any], node_raw: str, display_name: str) -> tuple[str, str]:
    display_name = validate_display_name(display_name)
    node_key = node_raw.strip().lower()
    if node_key in ("direct", "nat-direct"):
        target = state["direct_user"]
        key = "direct"
    else:
        if node_key.startswith("nat-"):
            node_key = node_key[4:]
        name = slugify(node_key)
        target = next((item for item in state.get("upstreams", []) if item["name"] == name), None)
        if target is None:
            raise NRMError(f"未找到节点：{node_raw}")
        key = name

    other_nodes = [("direct", state["direct_user"])] + [
        (item["name"], item) for item in state.get("upstreams", [])
    ]
    for other_key, other in other_nodes:
        if other_key == key:
            continue
        default_name = "NAT-Direct" if other_key == "direct" else f"NAT-{other_key}"
        other_name = other.get("display_name") or default_name
        if other_name.casefold() == display_name.casefold():
            raise NRMError(f"节点显示名称已被使用：{display_name}")

    target["display_name"] = display_name
    return key, display_name


def rename_node(node_raw: str, display_name: str | None = None) -> None:
    require_root()
    state = load_state()
    if display_name is None:
        display_name = input("新的节点显示名称：")
    if not display_name.strip():
        print("已取消，没有修改。")
        return
    key, value = set_node_display_name(state, node_raw, display_name)
    save_state(state)
    print(f"节点 {key} 的显示名称已修改为：{value}")
    print("此修改不会重启 sing-box。运行 nrm export 刷新 VLESS 分享链接；运行 nrm export --format mihomo 刷新 Mihomo 配置。")


def generate_reality_keys() -> tuple[str, str]:
    binary = shutil.which("sing-box")
    if not binary:
        raise NRMError("未找到 sing-box。请先运行安装脚本。")
    result = run([binary, "generate", "reality-keypair"])
    private_match = re.search(r"PrivateKey:\s*(\S+)", result.stdout)
    public_match = re.search(r"PublicKey:\s*(\S+)", result.stdout)
    if not private_match or not public_match:
        raise NRMError("无法解析 sing-box REALITY 密钥输出。")
    return private_match.group(1), public_match.group(1)


def check_sing_box_version() -> str:
    binary = shutil.which("sing-box")
    if not binary:
        raise NRMError("未安装 sing-box。请先运行安装脚本。")
    result = run([binary, "version"])
    match = re.search(r"\bversion\s+v?(\d+)\.(\d+)\.(\d+)", result.stdout)
    if not match:
        raise NRMError(f"无法识别 sing-box 版本：{result.stdout.strip()}")
    version = tuple(int(part) for part in match.groups())
    if version < MIN_SING_BOX:
        raise NRMError(f"需要 sing-box >= {'.'.join(map(str, MIN_SING_BOX))}，当前版本为 {'.'.join(map(str, version))}。")
    return ".".join(map(str, version))


def parse_vless_reality(link: str) -> dict[str, Any]:
    link = link.strip()
    if not link.lower().startswith("vless://"):
        raise NRMError("仅支持 vless:// 分享链接。")
    try:
        parsed = urllib.parse.urlsplit(link)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        host = parsed.hostname
        port = parsed.port
        user_uuid = parsed.username
    except ValueError as exc:
        raise NRMError(f"VLESS 链接格式错误：{exc}") from exc
    if parsed.scheme.lower() != "vless" or not host or not port or not user_uuid:
        raise NRMError("VLESS 链接缺少服务器地址、端口或 UUID。")
    try:
        user_uuid = str(uuid.UUID(urllib.parse.unquote(user_uuid)))
    except ValueError as exc:
        raise NRMError("VLESS UUID 格式不正确。") from exc
    transport = params.get("type", ["tcp"])[0].lower()
    security = params.get("security", [""])[0].lower()
    if transport not in ("", "tcp"):
        raise NRMError(f"不支持 {transport} 传输。NRM 仅接受原生 TCP。")
    if security != "reality":
        raise NRMError("上游必须使用 security=reality。TLS、none、WS、gRPC 等暂不接受。")
    flow = params.get("flow", [""])[0]
    if flow not in ("", "xtls-rprx-vision"):
        raise NRMError(f"不支持 flow={flow}。只接受空 flow 或 xtls-rprx-vision。")
    server_name = params.get("sni", params.get("serverName", [""]))[0]
    public_key = params.get("pbk", params.get("publicKey", [""]))[0]
    short_id = params.get("sid", params.get("shortId", [""]))[0]
    fingerprint = params.get("fp", ["chrome"])[0]
    if not server_name or not public_key or short_id is None:
        raise NRMError("REALITY 链接必须包含 sni、pbk 和 sid 参数。")
    server_name = validate_domain(server_name, "REALITY SNI")
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", public_key):
        raise NRMError("REALITY 公钥格式不正确。")
    try:
        decoded_key = base64.urlsafe_b64decode(public_key + "=" * ((4 - len(public_key) % 4) % 4))
    except Exception as exc:
        raise NRMError("REALITY 公钥不是有效的 Base64URL。") from exc
    if len(decoded_key) != 32:
        raise NRMError("REALITY 公钥解码后必须为 32 字节。")
    if not re.fullmatch(r"[0-9a-fA-F]{0,16}", short_id):
        raise NRMError("REALITY Short ID 必须是 0–16 位十六进制字符串。")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", fingerprint):
        raise NRMError("uTLS fingerprint 格式不正确。")
    label = urllib.parse.unquote(parsed.fragment) if parsed.fragment else ""
    return {
        "server": normalize_address(host),
        "server_port": validate_port(port, "上游端口"),
        "uuid": user_uuid,
        "flow": flow,
        "server_name": server_name,
        "public_key": public_key,
        "short_id": short_id,
        "fingerprint": fingerprint,
        "label": label,
    }


def dns_config() -> dict[str, Any]:
    return {
        "servers": [
            {
                "type": "https",
                "tag": "doh",
                "server": "1.1.1.1",
                "server_port": 443,
                "path": "/dns-query",
                "headers": {"Host": "cloudflare-dns.com"},
                "tls": {"enabled": True, "server_name": "cloudflare-dns.com"},
            }
        ],
        "final": "doh",
        "strategy": "ipv4_only",
        "cache_capacity": 1024,
    }


def upstream_outbound(upstream: dict[str, Any]) -> dict[str, Any]:
    outbound: dict[str, Any] = {
        "type": "vless",
        "tag": upstream["tag"],
        "server": upstream["server"],
        "server_port": int(upstream["server_port"]),
        "uuid": upstream["uuid"],
        "network": "tcp",
        "packet_encoding": "",
        "domain_resolver": "doh",
        "tls": {
            "enabled": True,
            "server_name": upstream["server_name"],
            "utls": {"enabled": True, "fingerprint": upstream.get("fingerprint", "chrome")},
            "reality": {
                "enabled": True,
                "public_key": upstream["public_key"],
                "short_id": upstream["short_id"],
            },
        },
    }
    if upstream.get("flow"):
        outbound["flow"] = upstream["flow"]
    return outbound


def build_config(state: dict[str, Any]) -> dict[str, Any]:
    users: list[dict[str, Any]] = [
        {"name": state["direct_user"]["name"], "uuid": state["direct_user"]["uuid"], "flow": "xtls-rprx-vision"}
    ]
    outbounds: list[dict[str, Any]] = [
        {"type": "direct", "tag": "direct"},
        {"type": "block", "tag": "block"},
    ]
    rules: list[dict[str, Any]] = [
        {"inbound": ["nrm-in"], "network": ["udp"], "action": "reject"},
        {
            "inbound": ["nrm-in"],
            "auth_user": [state["direct_user"]["name"]],
            "action": "route",
            "outbound": "direct",
        },
    ]
    for upstream in state.get("upstreams", []):
        if not upstream.get("enabled", True):
            continue
        users.append({"name": upstream["user_name"], "uuid": upstream["client_uuid"], "flow": "xtls-rprx-vision"})
        outbounds.append(upstream_outbound(upstream))
        rules.append(
            {
                "inbound": ["nrm-in"],
                "auth_user": [upstream["user_name"]],
                "action": "route",
                "outbound": upstream["tag"],
            }
        )
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": dns_config(),
        "inbounds": [
            {
                "type": "vless",
                "tag": "nrm-in",
                "listen": state.get("listen_address", "0.0.0.0"),
                "listen_port": int(state["listen_port"]),
                "users": users,
                "tls": {
                    "enabled": True,
                    "server_name": state["server_name"],
                    "reality": {
                        "enabled": True,
                        "handshake": {
                            "server": state["handshake_server"],
                            "server_port": int(state["handshake_port"]),
                            "domain_resolver": "doh",
                        },
                        "private_key": state["private_key"],
                        "short_id": [state["short_id"]],
                    },
                },
            }
        ],
        "outbounds": outbounds,
        "route": {"rules": rules, "final": "block"},
    }




def set_base_permissions() -> None:
    """Allow the dedicated systemd service account to traverse /etc/nrm.

    The directory is group-traversable, while state.json and backups remain
    root-only. In direct/container mode the directory stays private to root.
    """
    if systemd_available():
        if not shutil.which("getent"):
            raise NRMError("systemd 模式缺少 getent，无法安全设置 sing-box 配置目录权限。")
        group_result = subprocess.run(["getent", "group", "sing-box"], text=True, capture_output=True)
        if group_result.returncode != 0:
            raise NRMError("未找到 sing-box 系统组。请重新运行安装程序。")
        try:
            gid = int(group_result.stdout.strip().split(":")[2])
            os.chown(BASE, 0, gid)
            os.chmod(BASE, 0o750)
        except (ValueError, IndexError, PermissionError) as exc:
            raise NRMError(f"无法设置 /etc/nrm 目录权限：{exc}") from exc
    else:
        os.chmod(BASE, 0o700)


def set_config_permissions(path: Path) -> None:
    if systemd_available():
        group_result = subprocess.run(["getent", "group", "sing-box"], text=True, capture_output=True)
        if group_result.returncode != 0:
            raise NRMError("未找到 sing-box 系统组，无法授予服务读取配置的权限。")
        try:
            gid = int(group_result.stdout.strip().split(":")[2])
            os.chown(path, 0, gid)
            os.chmod(path, 0o640)
            return
        except (ValueError, IndexError, PermissionError) as exc:
            raise NRMError(f"无法设置 sing-box 配置文件权限：{exc}") from exc
    os.chmod(path, 0o600)


def write_and_check_config(state: dict[str, Any]) -> None:
    require_root()
    binary = shutil.which("sing-box")
    if not binary:
        raise NRMError("未找到 sing-box。")
    BASE.mkdir(parents=True, exist_ok=True)
    set_base_permissions()
    new_config = build_config(state)
    fd, temp_name = tempfile.mkstemp(prefix=".config.", suffix=".json", dir=str(BASE))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(new_config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temp_path, 0o600)
        result = run([binary, "check", "-c", str(temp_path)], timeout=60)
        backup_path = None
        if CONFIG_FILE.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = BASE / f"config.json.{stamp}.bak"
            shutil.copy2(CONFIG_FILE, backup_path)
            os.chmod(backup_path, 0o600)
        os.replace(temp_path, CONFIG_FILE)
        set_config_permissions(CONFIG_FILE)
        try:
            restart_service()
        except NRMError:
            if backup_path and backup_path.exists():
                shutil.copy2(backup_path, CONFIG_FILE)
                set_config_permissions(CONFIG_FILE)
                try:
                    restart_service()
                except NRMError:
                    pass
            else:
                CONFIG_FILE.unlink(missing_ok=True)
            raise
        print(result.stdout.strip() or "sing-box 配置检查通过。")
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def save_state(state: dict[str, Any]) -> None:
    atomic_json(STATE_FILE, state, 0o600)


def systemd_available() -> bool:
    return Path("/run/systemd/system").exists() and command_exists("systemctl")


def restart_service() -> None:
    if systemd_available() and SYSTEMD_UNIT.exists():
        run(["systemctl", "restart", "nrm.service"], timeout=45)
        result = run(["systemctl", "is-active", "nrm.service"], check=False)
        if result.returncode != 0:
            details = run(["journalctl", "-u", "nrm.service", "-n", "40", "--no-pager"], check=False)
            raise NRMError("nrm.service 重启后未处于 active 状态。\n" + (details.stdout or details.stderr or ""))
        return
    if PID_DIR.exists() and (PID_DIR / "enabled").exists():
        stop_direct()
        start_direct()


def start_direct() -> None:
    require_root()
    if not SUPERVISOR.exists():
        raise NRMError("找不到 direct-mode supervisor。请重新运行安装程序。")
    PID_DIR.mkdir(parents=True, exist_ok=True)
    (PID_DIR / "enabled").touch()
    if (PID_DIR / "supervisor.pid").exists():
        try:
            pid = int((PID_DIR / "supervisor.pid").read_text().strip())
            os.kill(pid, 0)
            print("NRM 已在运行。")
            return
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    with LOG_FILE.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(["/bin/sh", str(SUPERVISOR)], stdout=log, stderr=log, start_new_session=True)
    (PID_DIR / "supervisor.pid").write_text(str(proc.pid))
    os.chmod(PID_DIR / "supervisor.pid", 0o600)
    time.sleep(1)
    if proc.poll() is not None:
        raise NRMError("NRM supervisor 启动失败，请查看 /var/log/nrm.log。")
    print(f"NRM direct-mode 已启动，PID={proc.pid}")


def stop_direct() -> None:
    (PID_DIR / "enabled").unlink(missing_ok=True)
    child_file = PID_DIR / "child.pid"
    if child_file.exists():
        try:
            os.kill(int(child_file.read_text().strip()), signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        child_file.unlink(missing_ok=True)
    pid_file = PID_DIR / "supervisor.pid"
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        pid_file.unlink(missing_ok=True)
    print("NRM direct-mode 已停止。")


def service_status() -> str:
    if systemd_available() and SYSTEMD_UNIT.exists():
        result = run(["systemctl", "is-active", "nrm.service"], check=False)
        return result.stdout.strip() or "inactive"
    pid_file = PID_DIR / "supervisor.pid"
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text().strip()), 0)
            return "running (direct-mode; boot persistence depends on container init)"
        except (ValueError, ProcessLookupError, PermissionError):
            return "stopped"
    return "stopped"


def start_service() -> None:
    require_root()
    if systemd_available() and SYSTEMD_UNIT.exists():
        run(["systemctl", "enable", "--now", "nrm.service"], timeout=45)
        print("NRM 已启动并设置为开机启动。")
    else:
        start_direct()
        print("提示：无 systemd 的容器环境需要由容器启动命令或宿主机编排器保证开机启动。")


def stop_service() -> None:
    require_root()
    if systemd_available() and SYSTEMD_UNIT.exists():
        run(["systemctl", "stop", "nrm.service"], timeout=45)
        print("NRM 已停止；开机启动设置保持不变。")
    else:
        stop_direct()


def restart_service_command() -> None:
    require_root()
    if systemd_available() and SYSTEMD_UNIT.exists():
        run(["systemctl", "restart", "nrm.service"], timeout=45)
        print("NRM 已重启。")
    else:
        stop_direct()
        start_direct()


def init_wizard() -> None:
    require_root()
    check_sing_box_version()
    previous_state = None
    if STATE_FILE.exists():
        answer = input("检测到已有 NRM 配置。是否先备份并重新初始化？[y/N] ").strip().lower()
        if answer != "y":
            print("已取消。")
            return
        previous_state = load_state()
        backup()
    print("\nNRM 初始化：公网入口只需要一个 TCP 端口。")
    public_address = normalize_address(input("NAT 公网 IP / 域名：").strip())
    public_port = validate_port(input("NAT 公网映射端口 [46014]：").strip() or "46014", "公网映射端口")
    listen_port = validate_port(input("NAT 内部监听端口 [10000]：").strip() or "10000", "内部监听端口")
    if listen_port < 1024:
        raise NRMError("NRM 内部监听端口必须在 1024–65535 之间。")
    inbound_sni = validate_domain(input("NAT 入站 SNI [www.microsoft.com]：").strip() or "www.microsoft.com", "NAT 入站 SNI")
    handshake_server = validate_domain(input(f"NAT REALITY handshake.server [{inbound_sni}]（回车使用相同域名）：").strip() or inbound_sni, "NAT handshake.server")
    private_key, public_key = generate_reality_keys()
    short_id = os.urandom(4).hex()
    state = {
        "schema_version": 1, "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "public_address": public_address, "public_port": public_port, "listen_port": listen_port,
        "listen_address": "0.0.0.0", "server_name": inbound_sni, "handshake_server": handshake_server,
        "handshake_port": 443, "private_key": private_key, "public_key": public_key, "short_id": short_id,
        "fingerprint": "chrome", "direct_user": {"name":"nat-direct","uuid":str(uuid.uuid4()),"display_name":"NAT-Direct"},
        "upstreams": [],
        "port_mappings": [{"name":"nrm-proxy","protocol":"TCP","external_port":public_port,"internal_port":listen_port,"note":"NRM VLESS + REALITY 入站"}],
    }
    try:
        save_state(state)
        write_and_check_config(state)
    except Exception:
        if previous_state is not None:
            save_state(previous_state)
        else:
            STATE_FILE.unlink(missing_ok=True)
        raise
    print("\n初始化成功。")
    print(f"公网入口：{public_address}:{public_port} -> 本机 TCP {listen_port}")
    print("已创建 NAT-Direct。后续使用 nrm import 添加最多 5 条 VLESS REALITY 上游。")
    print(f"请在 NAT 服务商面板手动建立 TCP 映射：外部 {public_port} -> 内部 {listen_port}。")
    print("NAT 入站 SNI 与 handshake.server 已独立保存，可分别修改。")
    if systemd_available() and SYSTEMD_UNIT.exists():
        start_service()
    else:
        print("当前环境没有 systemd。配置已生成，请运行 nrm start 启动；容器重启后的自动启动由宿主机/容器编排器负责。")

def import_upstream() -> None:
    require_root()
    state = load_state()
    if len(state.get("upstreams", [])) >= MAX_UPSTREAMS:
        raise NRMError(f"当前版本最多管理 {MAX_UPSTREAMS} 条上游线路。")
    print("\n仅接受原生 TCP + REALITY 的 VLESS 分享链接。")
    name_raw = input(f"线路名称（例如 US-01，最多 {MAX_UPSTREAMS} 条）：").strip()
    name = slugify(name_raw)
    user_name = f"nat-{name}"
    tag = f"upstream-{name}"
    existing = {item["name"] for item in state.get("upstreams", [])}
    if name in existing or name == "direct":
        raise NRMError("线路名称已存在或为保留名称。")
    link = getpass.getpass("粘贴 VLESS 链接（输入内容不会显示）：").strip()
    parsed = parse_vless_reality(link)
    upstream = {
        "name": name,
        "display_name": f"NAT-{name}",
        "user_name": user_name,
        "tag": tag,
        "client_uuid": str(uuid.uuid4()),
        "enabled": True,
        "server": parsed["server"],
        "server_port": parsed["server_port"],
        "uuid": parsed["uuid"],
        "flow": parsed["flow"],
        "server_name": parsed["server_name"],
        "source_server_name": parsed["server_name"],
        "server_name_override": False,
        "public_key": parsed["public_key"],
        "short_id": parsed["short_id"],
        "fingerprint": parsed["fingerprint"],
        "source_label": parsed["label"],
    }
    state["upstreams"].append(upstream)
    save_state(state)
    try:
        write_and_check_config(state)
    except Exception:
        state["upstreams"].pop()
        save_state(state)
        raise
    print(f"已导入 {name}。客户端 UUID 已独立生成，NAT 上游 UUID 仅保存在本机配置中。")
    print("使用 `nrm export` 查看新节点分享链接。")


def remove_upstream(name_raw: str) -> None:
    require_root()
    state = load_state()
    name = slugify(name_raw)
    before = len(state.get("upstreams", []))
    old_state = load_state()
    state["upstreams"] = [item for item in state.get("upstreams", []) if item["name"] != name]
    if len(state["upstreams"]) == before:
        raise NRMError(f"未找到线路：{name}")
    save_state(state)
    try:
        write_and_check_config(state)
    except Exception:
        save_state(old_state)
        raise
    print(f"已删除线路：{name}")


def set_upstream_enabled(name_raw: str, enabled: bool) -> None:
    require_root()
    state = load_state()
    old_state = copy.deepcopy(state)
    name = slugify(name_raw)
    found = False
    for item in state.get("upstreams", []):
        if item["name"] == name:
            item["enabled"] = enabled
            found = True
            break
    if not found:
        raise NRMError(f"未找到线路：{name}")
    save_state(state)
    try:
        write_and_check_config(state)
    except Exception:
        save_state(old_state)
        raise
    print(f"线路 {name} 已{'启用' if enabled else '停用'}。")


def vless_link(state: dict[str, Any], name: str, user_uuid: str) -> str:
    address = state["public_address"]
    try:
        ip = ipaddress.ip_address(address)
        if ip.version == 6:
            address = f"[{address}]"
    except ValueError:
        pass
    params = {
        "encryption": "none",
        "security": "reality",
        "sni": state["server_name"],
        "fp": state.get("fingerprint", "chrome"),
        "pbk": state["public_key"],
        "sid": state["short_id"],
        "type": "tcp",
        "flow": "xtls-rprx-vision",
    }
    query = urllib.parse.urlencode(params, safe="-_")
    return f"vless://{user_uuid}@{address}:{state['public_port']}?{query}#{urllib.parse.quote(name, safe='-_.') }"


def export_links() -> list[str]:
    state = load_state()
    links = [vless_link(state, node_display_name(state, "direct"), state["direct_user"]["uuid"])]
    for item in state.get("upstreams", []):
        if item.get("enabled", True):
            links.append(vless_link(state, node_display_name(state, item), item["client_uuid"]))
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(EXPORT_DIR, 0o700)
    target = EXPORT_DIR / "nodes.txt"
    target.write_text("\n".join(links) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    return links


def export_mihomo() -> Path:
    state = load_state()
    proxies: list[dict[str, Any]] = []
    items = [(node_display_name(state, "direct"), state["direct_user"]["uuid"])]
    items += [(node_display_name(state, item), item["client_uuid"]) for item in state.get("upstreams", []) if item.get("enabled", True)]
    for name, user_uuid in items:
        proxy: dict[str, Any] = {
            "name": name,
            "type": "vless",
            "server": state["public_address"],
            "port": int(state["public_port"]),
            "uuid": user_uuid,
            "network": "tcp",
            "tls": True,
            "udp": False,
            "flow": "xtls-rprx-vision",
            "servername": state["server_name"],
            "client-fingerprint": state.get("fingerprint", "chrome"),
            "reality-opts": {"public-key": state["public_key"], "short-id": state["short_id"]},
        }
        proxies.append(proxy)
    names = [item["name"] for item in proxies]
    data = {
        "proxies": proxies,
        "proxy-groups": [{"name": "NRM-出口选择", "type": "select", "proxies": names}],
        "rules": ["MATCH,NRM-出口选择"],
    }
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(EXPORT_DIR, 0o700)
    target = EXPORT_DIR / "mihomo.yaml"
    # JSON is a valid YAML subset and avoids a PyYAML runtime dependency.
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    return target


def set_nat_sni(inbound: str | None = None, handshake: str | None = None) -> None:
    require_root()
    state = load_state()
    old_state = copy.deepcopy(state)
    if inbound is None and handshake is None:
        inbound = input(f"NAT 入站 SNI [{state['server_name']}]（回车不修改）：").strip() or None
        handshake = input(f"NAT handshake.server [{state['handshake_server']}]（回车不修改）：").strip() or None
    if inbound:
        state["server_name"] = validate_domain(inbound, "NAT 入站 SNI")
    if handshake:
        state["handshake_server"] = validate_domain(handshake, "NAT handshake.server")
    if state == old_state:
        print("没有修改。")
        return
    save_state(state)
    try:
        write_and_check_config(state)
    except Exception:
        save_state(old_state)
        raise
    print(f"NAT 入站 SNI：{state['server_name']}")
    print(f"NAT handshake.server：{state['handshake_server']}:443")


def set_upstream_sni(name_raw: str, domain: str | None = None, reset: bool = False) -> None:
    require_root()
    state = load_state()
    old_state = copy.deepcopy(state)
    name = slugify(name_raw)
    item = next((x for x in state.get("upstreams", []) if x["name"] == name), None)
    if item is None:
        raise NRMError(f"未找到线路：{name}")
    source_sni = item.get("source_server_name") or item["server_name"]
    if reset:
        if domain:
            raise NRMError("使用 --reset 时不要同时指定域名。")
        item["server_name"] = source_sni
        item["server_name_override"] = False
    else:
        if domain is None:
            domain = input(f"线路 {name} 当前 SNI 为 {item['server_name']}；输入新 SNI（回车取消）：").strip()
        if not domain:
            print("已取消，没有修改。")
            return
        item["server_name"] = validate_domain(domain, "上游 SNI")
        item["server_name_override"] = item["server_name"] != source_sni
    if state == old_state:
        print("SNI 没有变化。")
        return
    save_state(state)
    try:
        write_and_check_config(state)
    except Exception:
        save_state(old_state)
        raise
    mode = "自动读取链接值" if not item["server_name_override"] else "手动覆盖值"
    print(f"线路 {name} 的上游 SNI 已更新为 {item['server_name']}（{mode}）。")


def list_nodes() -> None:
    state = load_state()
    print(f"NAT 入口：{state['public_address']}:{state['public_port']} -> TCP {state['listen_port']}")
    print(f"NAT 入站 SNI：{state['server_name']} | handshake.server：{state['handshake_server']}:{state.get('handshake_port', 443)}")
    print(f"Short ID：{state['short_id']}")
    print("\n节点：")
    print(f"  {'显示名称':<24} {'内部标识':<14} {'启用':<6} {'出口'}")
    print(f"  {'-'*24} {'-'*14} {'-'*6} {'-'*24}")
    print(f"  {node_display_name(state, 'direct'):<24} {'direct':<14} {'是':<6} NAT 本机 Direct")
    for item in state.get("upstreams", []):
        status = "是" if item.get("enabled", True) else "否"
        endpoint = f"{item['server']}:{item['server_port']}"
        mode = "手动覆盖" if item.get("server_name_override") else "自动"
        print(f"  {node_display_name(state, item):<24} {item['name']:<14} {status:<6} {endpoint} SNI={item['server_name']}（{mode}）")

def make_port_mapping(name_raw: str, external_port: int | str, internal_port: int | str, note: str = "") -> dict[str, Any]:
    name = slugify(name_raw)
    if name == "nrm-proxy":
        raise NRMError("nrm-proxy 是 NRM 主入口保留名称，不能作为普通端口规则添加。")
    note = str(note).strip()
    if len(note) > 80 or any(ch in note for ch in "\r\n"):
        raise NRMError("备注最多 80 个字符，不能包含换行。")
    return {"name":name,"protocol":"TCP","external_port":validate_port(external_port,"外部端口"),"internal_port":validate_port(internal_port,"内部端口"),"note":note}


def port_list() -> None:
    state = load_state()
    print("NAT TCP 端口映射记录（本地登记；真实转发仍需在服务商面板配置）")
    print(f"{'名称':<16} {'协议':<6} {'外部端口':<10} {'内部端口':<10} 备注")
    print("-" * 76)
    for item in state.get("port_mappings", []):
        print(f"{item['name']:<16} TCP    {int(item['external_port']):<10} {int(item['internal_port']):<10} {item.get('note', '')}")
    print("NRM 不会自动操作 NAT 服务商后台；请按上表建立 TCP 外部端口 -> 内部端口映射。")


def port_add(name: str, external_port: int, internal_port: int, note: str = "") -> None:
    require_root()
    state = load_state()
    item = make_port_mapping(name, external_port, internal_port, note)
    mappings = state.setdefault("port_mappings", [])
    if any(x.get("name") == item["name"] for x in mappings):
        raise NRMError(f"端口规则名称已存在：{item['name']}")
    if any(int(x["external_port"]) == item["external_port"] for x in mappings):
        raise NRMError(f"外部端口 {item['external_port']} 已被其他规则使用。")
    mappings.append(item)
    save_state(state)
    print(f"已登记 TCP {item['external_port']} -> {item['internal_port']}（{item['name']}）。请同步到 NAT 服务商面板。")


def port_edit(name: str, external_port: int | None = None, internal_port: int | None = None, note: str | None = None) -> None:
    require_root()
    state = load_state()
    old_state = copy.deepcopy(state)
    slug = slugify(name)
    item = next((x for x in state.get("port_mappings", []) if x["name"] == slug), None)
    if item is None:
        raise NRMError(f"未找到端口规则：{slug}")
    if external_port is None and internal_port is None and note is None:
        raw = input(f"新外部端口 [{item['external_port']}]（回车保持）：").strip()
        external_port = validate_port(raw, "外部端口") if raw else None
        raw = input(f"新内部端口 [{item['internal_port']}]（回车保持）：").strip()
        internal_port = validate_port(raw, "内部端口") if raw else None
        raw = input(f"新备注 [{item.get('note', '')}]（回车保持）：").strip()
        note = raw if raw else None
    new_external = validate_port(external_port, "外部端口") if external_port is not None else int(item["external_port"])
    new_internal = validate_port(internal_port, "内部端口") if internal_port is not None else int(item["internal_port"])
    if slug == "nrm-proxy" and new_internal < 1024:
        raise NRMError("NRM 内部监听端口必须在 1024–65535 之间。")
    if any(x["name"] != slug and int(x["external_port"]) == new_external for x in state["port_mappings"]):
        raise NRMError(f"外部端口 {new_external} 已被其他规则使用。")
    item["external_port"], item["internal_port"] = new_external, new_internal
    if note is not None:
        if len(note) > 80 or any(ch in note for ch in "\r\n"):
            raise NRMError("备注最多 80 个字符，不能包含换行。")
        item["note"] = note.strip()
    changed = slug == "nrm-proxy" and (old_state["public_port"] != new_external or old_state["listen_port"] != new_internal)
    if slug == "nrm-proxy":
        state["public_port"], state["listen_port"] = new_external, new_internal
    save_state(state)
    if changed:
        try:
            write_and_check_config(state)
        except Exception:
            save_state(old_state)
            raise
        print("NRM 主入口已更新。请同步修改 NAT 面板映射，并重新导出客户端链接。")
    else:
        print(f"端口规则 {slug} 已更新。请同步检查 NAT 面板映射。")


def port_remove(name: str) -> None:
    require_root()
    state = load_state()
    slug = slugify(name)
    if slug == "nrm-proxy":
        raise NRMError("不能删除 NRM 主入口规则。")
    before = len(state.get("port_mappings", []))
    state["port_mappings"] = [x for x in state.get("port_mappings", []) if x["name"] != slug]
    if len(state["port_mappings"]) == before:
        raise NRMError(f"未找到端口规则：{slug}")
    save_state(state)
    print(f"已删除本地规则记录 {slug}；真实映射请到服务商面板单独删除。")


def port_batch() -> None:
    require_root()
    state = load_state()
    names = {x["name"] for x in state.get("port_mappings", [])}
    ports = {int(x["external_port"]) for x in state.get("port_mappings", [])}
    additions = []
    print("每行输入：名称,外部端口,内部端口,备注；空行结束。仅登记 TCP。")
    while True:
        line = input("> ").strip()
        if not line:
            break
        parts = [x.strip() for x in line.split(",", 3)]
        if len(parts) < 3:
            print("格式错误。")
            continue
        try:
            item = make_port_mapping(parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else "")
            if item["name"] in names or item["name"] in {x["name"] for x in additions}:
                raise NRMError("规则名称重复。")
            if item["external_port"] in ports or item["external_port"] in {x["external_port"] for x in additions}:
                raise NRMError("外部端口重复。")
            additions.append(item)
            print(f"已暂存：{item['name']} TCP {item['external_port']} -> {item['internal_port']}")
        except NRMError as exc:
            print(f"未添加：{exc}")
    if additions:
        state.setdefault("port_mappings", []).extend(additions)
        save_state(state)
        print(f"已保存 {len(additions)} 条规则。请到 NAT 服务商面板逐条建立映射。")
    else:
        print("没有新增规则。")


def port_check() -> None:
    state = load_state()
    if not command_exists("ss"):
        raise NRMError("未找到 ss 命令，请安装 iproute2。")
    result = run(["ss", "-H", "-ltn"], check=False)
    if result.returncode != 0:
        raise NRMError("读取本机 TCP 监听端口失败。")
    listening = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4:
            try:
                listening.add(int(fields[3].rsplit(":", 1)[1]))
            except (ValueError, IndexError):
                pass
    print("内部 TCP 监听检查：")
    for item in state.get("port_mappings", []):
        port = int(item["internal_port"])
        print(f"  {item['name']:<16} 外部 {int(item['external_port']):<6} -> 内部 {port:<6} {'LISTEN' if port in listening else '未监听'}")
    print("此检查只能验证 NAT 主机内部监听，不能证明服务商外部端口映射已生效。")


def port_menu() -> None:
    while True:
        print("\n========== NAT TCP 端口映射 ==========")
        print("1) 查看  2) 添加  3) 批量添加  4) 编辑  5) 删除记录  6) 检查监听  0) 返回")
        choice = input("请选择：").strip()
        try:
            if choice == "1": port_list()
            elif choice == "2": port_add(input("名称：").strip(), validate_port(input("外部端口：").strip()), validate_port(input("内部端口：").strip()), input("备注：").strip())
            elif choice == "3": port_batch()
            elif choice == "4": port_edit(input("规则名称：").strip())
            elif choice == "5": port_remove(input("规则名称：").strip())
            elif choice == "6": port_check()
            elif choice == "0": return
            else: print("无效选项。")
        except (NRMError, KeyboardInterrupt) as exc:
            print(f"\n错误：{exc}")


def make_test_config(state: dict[str, Any], outbound: dict[str, Any], port: int) -> dict[str, Any]:
    return {
        "log": {"level": "error"},
        "dns": dns_config(),
        "inbounds": [{"type": "mixed", "tag": "test-in", "listen": "127.0.0.1", "listen_port": port}],
        "outbounds": ([outbound] if outbound.get("tag") == "direct" else [outbound, {"type": "direct", "tag": "direct"}]) + [{"type": "block", "tag": "block"}],
        "route": {"rules": [{"inbound": ["test-in"], "network": ["udp"], "action": "reject"}], "final": outbound["tag"]},
    }


def test_one(state: dict[str, Any], name: str, outbound: dict[str, Any]) -> tuple[bool, str]:
    binary = shutil.which("sing-box")
    curl = shutil.which("curl")
    if not binary or not curl:
        raise NRMError("测试需要 sing-box 和 curl。")
    port = random.randint(20000, 45000)
    config = make_test_config(state, outbound, port)
    with tempfile.TemporaryDirectory(prefix="nrm-test-") as temp_dir:
        config_path = Path(temp_dir) / "test.json"
        log_path = Path(temp_dir) / "test.log"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        checked = subprocess.run([binary, "check", "-c", str(config_path)], text=True, capture_output=True, timeout=30)
        if checked.returncode != 0:
            return False, "配置检查失败：" + (checked.stderr or checked.stdout).strip()
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.Popen([binary, "run", "-c", str(config_path)], stdout=log, stderr=log)
            try:
                ready = False
                for _ in range(30):
                    if proc.poll() is not None:
                        break
                    with socket.socket() as sock:
                        sock.settimeout(0.3)
                        if sock.connect_ex(("127.0.0.1", port)) == 0:
                            ready = True
                            break
                    time.sleep(0.25)
                if not ready:
                    details = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
                    return False, "测试代理未能启动：" + details
                result = subprocess.run(
                    [curl, "--silent", "--show-error", "--fail", "--socks5-hostname", f"127.0.0.1:{port}", "--connect-timeout", "8", "--max-time", "20", "https://api.ipify.org"],
                    text=True,
                    capture_output=True,
                    timeout=25,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return True, result.stdout.strip()
                return False, (result.stderr or result.stdout or "出口测试失败").strip()
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)


def test_routes() -> bool:
    state = load_state()
    print("\n正在测试 NAT 本机出口（TCP/DoH）……")
    failed = False
    direct = {"type": "direct", "tag": "direct"}
    ok, detail = test_one(state, "NAT-Direct", direct)
    print(f"{'[OK]' if ok else '[FAIL]'} NAT-Direct: {detail}")
    failed = failed or not ok
    for item in state.get("upstreams", []):
        if not item.get("enabled", True):
            print(f"[SKIP] NAT-{item['name']}: 已停用")
            continue
        print(f"正在测试 NAT-{item['name']}……")
        ok, detail = test_one(state, item["name"], upstream_outbound(item))
        print(f"{'[OK]' if ok else '[FAIL]'} NAT-{item['name']}: {detail}")
        failed = failed or not ok
    return not failed

def backup() -> Path:
    require_root()
    if not STATE_FILE.exists():
        raise NRMError("没有可备份的 NRM 配置。")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = BACKUP_DIR / f"nrm-{stamp}.tar.gz"
    import tarfile
    with tarfile.open(target, "w:gz") as archive:
        archive.add(STATE_FILE, arcname="state.json")
        if CONFIG_FILE.exists():
            archive.add(CONFIG_FILE, arcname="config.json")
    os.chmod(target, 0o600)
    print(f"已创建 root-only 备份：{target}")
    return target


def doctor() -> None:
    print(f"{APP} v{VERSION}")
    print(f"系统：{sys.platform} | Python：{sys.version.split()[0]}")
    print(f"服务管理：{'systemd' if systemd_available() else 'direct-mode'}")
    if command_exists("sing-box"):
        try:
            print("sing-box：" + check_sing_box_version())
        except NRMError as exc:
            print("sing-box：" + str(exc))
    else:
        print("sing-box：未安装")
    if command_exists("free"):
        print(run(["free", "-h"]).stdout.strip())
    if command_exists("df"):
        print(run(["df", "-h", str(BASE if BASE.exists() else "/")]).stdout.strip())
    if STATE_FILE.exists():
        try:
            state = load_state()
            print(f"配置：已初始化 | 上游线路：{len(state.get('upstreams', []))}/{MAX_UPSTREAMS} | TCP 端口规则：{len(state.get('port_mappings', []))}")
            if command_exists("sing-box") and CONFIG_FILE.exists():
                result = run(["sing-box", "check", "-c", str(CONFIG_FILE)], check=False)
                print("配置检查：" + ("通过" if result.returncode == 0 else "失败：" + (result.stderr or result.stdout).strip()))
        except NRMError as exc:
            print("配置：" + str(exc))
    else:
        print("配置：未初始化")
    print("服务状态：" + service_status())


def logs() -> None:
    if systemd_available() and SYSTEMD_UNIT.exists() and command_exists("journalctl"):
        os.execvp("journalctl", ["journalctl", "-u", "nrm.service", "-n", "100", "-f", "--no-pager"])
    if LOG_FILE.exists():
        os.execvp("tail", ["tail", "-n", "100", "-f", str(LOG_FILE)])
    raise NRMError("暂无日志。")


def menu() -> None:
    while True:
        print("\n========== NAT Relay Manager ==========")
        print("1) 初始化 NAT 入口")
        print("2) 导入 VLESS TCP + REALITY 上游（最多 5 条）")
        print("3) 查看节点")
        print("4) 删除上游")
        print("5) 启用 / 停用上游")
        print("6) 测试所有出口 IP")
        print("7) 导出分享链接")
        print("8) 导出 Mihomo 配置")
        print("9) 检查 / 应用配置")
        print("10) 服务状态")
        print("11) 重启服务")
        print("12) 查看日志")
        print("13) 备份配置")
        print("14) 系统诊断")
        print("15) NAT TCP 端口映射管理")
        print("16) 修改 NAT 入站 SNI / handshake.server")
        print("17) 修改美国 VPS 上游 SNI")
        print("18) 修改节点显示名称")
        print("0) 退出")
        choice = input("请选择：").strip()
        try:
            if choice == "1": init_wizard()
            elif choice == "2": import_upstream()
            elif choice == "3": list_nodes()
            elif choice == "4": remove_upstream(input("要删除的线路名称："))
            elif choice == "5":
                name = input("线路名称：").strip()
                action = input("启用还是停用？[e/d]：").strip().lower()
                set_upstream_enabled(name, action == "e")
            elif choice == "6": test_routes()
            elif choice == "7":
                for link in export_links(): print(link)
                print(f"\n已保存到 {EXPORT_DIR / 'nodes.txt'}（权限 600）")
            elif choice == "8": print(f"已导出：{export_mihomo()}")
            elif choice == "9": write_and_check_config(load_state())
            elif choice == "10": print("服务状态：" + service_status())
            elif choice == "11": restart_service_command()
            elif choice == "12": logs()
            elif choice == "13": backup()
            elif choice == "14": doctor()
            elif choice == "15": port_menu()
            elif choice == "16": set_nat_sni()
            elif choice == "17": set_upstream_sni(input("线路名称：").strip())
            elif choice == "18":
                node_ref = input("节点内部标识（direct / us-01）：").strip()
                new_name = input("新的显示名称：")
                rename_node(node_ref, new_name)
            elif choice == "0": return
            else: print("无效选项。")
        except (NRMError, KeyboardInterrupt) as exc:
            print(f"\n错误：{exc}")

def main() -> int:
    parser = argparse.ArgumentParser(prog="nrm", description="NAT Relay Manager - TCP-only VLESS/REALITY relay manager")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("init", help="初始化 NAT 入口")
    sub.add_parser("import", aliases=["add"], help="导入 VLESS TCP + REALITY 上游")
    sub.add_parser("list", help="列出节点")
    rp = sub.add_parser("remove", help="删除上游线路"); rp.add_argument("name")
    ep = sub.add_parser("enable", help="启用上游线路"); ep.add_argument("name")
    dp = sub.add_parser("disable", help="停用上游线路"); dp.add_argument("name")
    sub.add_parser("test", help="测试所有出口")
    xp = sub.add_parser("export", help="导出客户端配置"); xp.add_argument("--format", choices=["links","mihomo"], default="links")
    sub.add_parser("apply", help="检查并应用配置")
    sub.add_parser("start", help="启动服务")
    sub.add_parser("stop", help="停止服务（保留开机启动）")
    sub.add_parser("restart", help="重启服务")
    sub.add_parser("status", help="查看服务状态")
    sub.add_parser("logs", help="查看日志")
    sub.add_parser("backup", help="备份配置")
    sub.add_parser("doctor", help="系统诊断")
    ns = sub.add_parser("nat-sni", help="分别修改 NAT 入站 SNI 和 handshake.server")
    ns.add_argument("--inbound", help="NAT 入站 SNI"); ns.add_argument("--handshake", help="NAT handshake.server")
    us = sub.add_parser("upstream-sni", help="修改或恢复上游 SNI")
    us.add_argument("name"); us.add_argument("domain", nargs="?"); us.add_argument("--reset", action="store_true")
    rn = sub.add_parser("rename-node", aliases=["rename"], help="修改节点显示名称")
    rn.add_argument("name", help="direct、us-01 或 NAT-us-01")
    rn.add_argument("display_name", help="新的显示名称，支持中文")
    pp = sub.add_parser("port", aliases=["ports"], help="管理 NAT TCP 端口映射")
    ps = pp.add_subparsers(dest="port_action")
    ps.add_parser("list", help="查看规则")
    pa = ps.add_parser("add", help="登记规则"); pa.add_argument("name"); pa.add_argument("external_port", type=int); pa.add_argument("internal_port", type=int); pa.add_argument("--note", default="")
    pe = ps.add_parser("edit", help="编辑规则"); pe.add_argument("name"); pe.add_argument("--external-port", type=int); pe.add_argument("--internal-port", type=int); pe.add_argument("--note")
    pr = ps.add_parser("remove", help="删除规则记录"); pr.add_argument("name")
    ps.add_parser("batch", help="批量登记"); ps.add_parser("check", help="检查内部监听")
    sub.add_parser("menu", help="打开交互菜单")
    args = parser.parse_args()
    try:
        if args.command in (None, "menu"): menu()
        elif args.command == "init": init_wizard()
        elif args.command in ("import", "add"): import_upstream()
        elif args.command == "list": list_nodes()
        elif args.command == "remove": remove_upstream(args.name)
        elif args.command == "enable": set_upstream_enabled(args.name, True)
        elif args.command == "disable": set_upstream_enabled(args.name, False)
        elif args.command == "test": return 0 if test_routes() else 2
        elif args.command == "export":
            if args.format == "links":
                for link in export_links(): print(link)
                print(f"\n已保存到 {EXPORT_DIR / 'nodes.txt'}（权限 600）")
            else: print(export_mihomo())
        elif args.command == "apply": write_and_check_config(load_state())
        elif args.command == "start": start_service()
        elif args.command == "stop": stop_service()
        elif args.command == "restart": restart_service_command()
        elif args.command == "status": print("服务状态：" + service_status())
        elif args.command == "logs": logs()
        elif args.command == "backup": backup()
        elif args.command == "doctor": doctor()
        elif args.command == "nat-sni": set_nat_sni(args.inbound, args.handshake)
        elif args.command == "upstream-sni": set_upstream_sni(args.name, args.domain, args.reset)
        elif args.command in ("rename-node", "rename"): rename_node(args.name, args.display_name)
        elif args.command in ("port", "ports"):
            if args.port_action in (None, "list"): port_list()
            elif args.port_action == "add": port_add(args.name,args.external_port,args.internal_port,args.note)
            elif args.port_action == "edit": port_edit(args.name,args.external_port,args.internal_port,args.note)
            elif args.port_action == "remove": port_remove(args.name)
            elif args.port_action == "batch": port_batch()
            elif args.port_action == "check": port_check()
        return 0
    except (NRMError, KeyboardInterrupt) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
