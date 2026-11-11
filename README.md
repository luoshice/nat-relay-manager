# NAT Relay Manager (NRM)

**面向小内存 NAT VPS 的 TCP-only VLESS/REALITY 中转管理器。**

NRM 使用一个 sing-box 进程，在 NAT 上建立一个 VLESS + TCP + REALITY 入站，通过不同 UUID 将客户端请求分别路由到 NAT 主机 Direct 出口或最多 5 条独立 VLESS + TCP + REALITY 上游出口。所有客户端节点共用同一个 NAT 公网 TCP 端口，因此通常只需要一条 NAT 端口映射。

> 当前版本面向自有服务器和获授权网络。请先在测试 NAT 上验证，再迁移生产流量。

## 一键安装

在 Debian / Ubuntu NAT VPS 上执行：

```bash
curl -fsSL https://raw.githubusercontent.com/luoshice/nat-relay-manager/main/install.sh -o /tmp/nrm-install.sh
sudo bash /tmp/nrm-install.sh
```

安装器会安装 sing-box 官方 APT 软件源、下载项目、检查源码并询问是否初始化。请先检查脚本，不建议未经审查直接 `curl | bash`。

## 功能

- 一个 VLESS + TCP + REALITY NAT 入站；一个 Direct 用户和最多 5 个 Relay 用户。
- 按 VLESS 用户名路由；未匹配用户 fail-closed 到 block。
- 入站 UDP 拒绝；上游 outbound TCP-only；Cloudflare DoH over HTTPS/TCP。
- VLESS TCP + REALITY 上游链接导入，单独启停、删除、测速。
- 导出 VLESS 分享链接与 Mihomo 配置。
- sing-box 配置校验、备份、systemd 与 direct-mode supervisor。

五条 VLESS 上游不一定是五个不同公网出口 IP；取决于各上游服务器的出口配置。

## NAT 端口映射

初始化时输入公网地址、公网映射端口（例如 46014）和本机监听端口（默认 10000）。在 NAT 面板配置一条 TCP 映射，例如 `46014 -> 10000/TCP`。不要与 SSH 端口冲突。

## 日常管理

```bash
nrm                 # 交互菜单
nrm init            # 初始化入口
nrm import          # 导入上游 VLESS 链接（输入不回显）
nrm list            # 列出节点
nrm test            # 测试出口 IP
nrm export          # 导出 VLESS 节点
nrm export --format mihomo
nrm disable us-03
nrm enable us-03
nrm remove us-03
nrm apply
nrm status
nrm restart
nrm logs
nrm backup
nrm doctor
```

仅支持原生 TCP + REALITY；不支持 WS、gRPC、Hysteria2、TUIC、普通 TLS 或 UDP 传输。上游服务器必须已部署并提供 VLESS 分享链接；NRM 不负责部署上游 VPS。

## 支持范围与注意事项

- Debian / Ubuntu（apt）；x86_64/amd64、aarch64/arm64。
- sing-box 1.14.0 或更高版本。
- systemd；无 systemd 时使用 direct-mode supervisor。容器重启持久性由宿主机/编排器负责。
- 128 MB 内存仍然紧张。`GOMEMLIMIT=40MiB` 是 Go runtime 软目标，不是进程硬限制；apt 安装过程可能需要额外内存。
- TCP-only 指 NRM 代理数据链路；操作系统及其他服务仍可能使用 UDP。
- 客户端关闭 QUIC/HTTP3 或允许回退 TCP；依赖 UDP 的应用不在支持范围内。

## 安全

- `/etc/nrm/state.json` 保存上游 UUID 和 NAT REALITY 私钥，权限为 0600。
- 不要把 `/etc/nrm/`、导出的分享链接、私钥或备份上传到 GitHub。
- 更换 NAT 后建议重新初始化并重新导入上游链接；更换公网地址或端口后重新导出客户端链接。

## 开发与测试

`nrm.py.gz.b64` 是 Python 主程序源码的 gzip+Base64 存档。解码后运行：

```bash
base64 -d nrm.py.gz.b64 | gzip -dc > nrm.py
python3 -m unittest discover -s tests -v
python3 -m py_compile nrm.py
bash -n install.sh supervisor.sh
```

## 许可证

MIT。sing-box 由 SagerNet 独立开发和发布；NRM 不包含或重新分发 sing-box 二进制。
