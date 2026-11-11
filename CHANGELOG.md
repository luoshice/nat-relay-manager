# Changelog

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
