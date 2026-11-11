# NAT Relay Manager (NRM)

轻量 TCP-only VLESS + TCP + REALITY NAT 中转管理器。单个 sing-box 进程、一个 NAT 入站端口、一个 Direct 用户和最多 5 条独立上游出口。

## 安装

Debian / Ubuntu：

```bash
curl -fsSL https://raw.githubusercontent.com/luoshice/nat-relay-manager/main/install.sh -o /tmp/nrm-install.sh
sudo bash /tmp/nrm-install.sh
```

## 功能

- 一个 VLESS + TCP + REALITY NAT 入站，Vision flow，按用户路由到 NAT-Direct 或最多 5 条上游。
- 入站 UDP 拒绝、上游 TCP-only、DoH over HTTPS/TCP、未匹配用户 fail-closed。
- NAT 入站 SNI 与 REALITY `handshake.server` 分开设置，初始化时默认相同。
- 导入每条上游时自动读取 VLESS 链接自己的 SNI；支持手动覆盖和恢复原值。
- NAT TCP 端口规则：添加、批量添加、编辑、删除、列表、内部监听检查。
- 上游启停、删除、出口测试、VLESS 分享链接与 Mihomo 导出。
- 兼容已有 schema-v1 配置；配置备份、诊断、systemd / direct-mode 服务管理。

## NAT 端口映射

初始化时输入服务商分配的公网端口和 NAT 本机监听端口。例如 NAT 面板填写：

| 字段 | 示例 |
|---|---:|
| 协议 | TCP |
| 外部端口 | 53993 |
| 内部端口 | 10000 |

NRM 初始化对应填公网端口 53993、内部监听端口 10000。所有 Relay 节点共用一个入口，不必为每条美国 VPS 上游分别建映射。

额外规则示例：

```bash
nrm port add ssh 46013 22 --note "SSH"
nrm port list
nrm port check
nrm port batch
nrm port edit ssh --external-port 46014
nrm port remove ssh
```

批量输入每行格式：`名称,外部端口,内部端口,备注`，空行结束。**端口管理只维护本机规则清单并检查内部监听，不会自动操作 NAT 服务商后台。** 实际 TCP 映射仍需在服务商面板手动创建或修改。NRM 不登记 UDP 规则。

## SNI

初始化分别询问 NAT 入站 SNI 与 `handshake.server`，第二项回车沿用第一项：

```bash
nrm nat-sni
nrm nat-sni --inbound inbound.example.com --handshake handshake.example.com
nrm upstream-sni us-01 custom.example.com
nrm upstream-sni us-01 --reset
```

上游 SNI 默认自动读取导入链接中的 `sni` 参数。手动覆盖只影响指定线路；`--reset` 恢复导入时的原始值。

## 常用命令

```bash
nrm
nrm init
nrm import
nrm list
nrm test
nrm export
nrm export --format mihomo
nrm port list
nrm nat-sni
nrm upstream-sni us-01 --reset
nrm backup
nrm doctor
nrm status
nrm restart
nrm stop
nrm logs
```

仅支持原生 TCP + REALITY；不支持 WS、gRPC、Hysteria2、TUIC、普通 TLS 或 UDP 传输。上游 VPS 需预先部署并提供 VLESS 分享链接。

## 注意

- Debian / Ubuntu（apt），amd64/x86_64、arm64/aarch64；sing-box 1.14.0+。
- 128 MB 内存仍然紧张；Go runtime 内存软限制不是进程硬限制。
- `nrm port check` 只能验证 NAT 主机内部监听，不能证明服务商外部映射已生效。
- 不要把 `/etc/nrm/`、UUID、分享链接、REALITY 私钥或备份上传到 GitHub。
- 更换 NAT 公网地址或端口后，重新导出客户端链接。

## 测试

```bash
python3 -m py_compile nrm.py
python3 -m unittest discover -s tests -v
bash -n install.sh supervisor.sh
```

MIT License。
