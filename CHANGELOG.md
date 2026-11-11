# Changelog

## 0.3.3 - 2026-11-11

- Simplified the Podman/container systemd unit after reproducing the documented `Failed to keep CAP_SYS_ADMIN` / `217/USER` failure pattern.
- Removed LockPersonality, RestrictSUIDSGID and RestrictAddressFamilies from the container-only unit; retained the dedicated unprivileged sing-box account, NoNewPrivileges and an empty capability set.
- Added systemd start-rate limiting to prevent repeated restart loops and log storms.
- Kept the fully hardened regular-host unit unchanged.

## 0.3.2 - 2026-11-11

- Fixed Podman/container systemd startup by explicitly clearing the service capability bounding and ambient sets.
- sing-box continues to run as the dedicated `sing-box` user; no Linux capabilities are needed for the configured high TCP listener port or outbound TCP relay.
- Kept non-privilege hardening controls and the regular-host service template unchanged.

## 0.3.1 - 2026-11-11

- Detect Podman, Docker, LXC and other systemd-detect-virt container environments during installation.
- Install a container-compatible systemd unit that avoids mount-namespace sandbox features requiring CAP_SYS_ADMIN.
- Keep sing-box running as its dedicated unprivileged user and retain non-namespace hardening controls.
- Preserve the full hardened unit for regular systemd hosts.

## 0.3.0 - 2026-11-11

- Added custom display names for NAT-Direct and each upstream, including Chinese names.
- Added interactive menu item 18 and `nrm rename-node` CLI command.
- VLESS share-link fragments and Mihomo proxy names now use display names.
- Kept internal route IDs, tags, UUIDs and sing-box runtime configuration unchanged when renaming.
- Added backward-compatible defaults and duplicate / control-character validation.

## 0.2.0 - 2026-11-11

- Added TCP port mapping registry with add, batch, edit, remove, list and local listener checks.
- Separated NAT inbound SNI from REALITY handshake.server.
- Preserved imported upstream SNI and added per-line override/reset.
- Added backward-compatible defaults for existing schema-v1 state.
- Safer reinitialization rollback; stopping NRM no longer disables boot startup.
- Installer no longer disables an existing sing-box.service automatically.
- Added plain nrm.py source and synchronized gzip/Base64 mirrors.
- Expanded tests for migration, port rules, SNI and Short ID boundaries.

## 0.1.0 - 2026-11-11

- Initial TCP-only VLESS + REALITY NAT relay manager.
