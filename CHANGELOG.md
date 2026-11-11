# Changelog

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
