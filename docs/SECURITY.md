# Security Notes

1. Run NRM only on servers and networks you own or are authorized to administer.
2. Treat VLESS links as credentials. They contain upstream UUIDs and connection metadata.
3. Never upload `/etc/nrm/state.json`, `/etc/nrm/config.json`, exported links, private keys, or backups to GitHub.
4. The NAT Reality private key is generated locally and is not shared with upstream servers.
5. Upstream Reality public keys are public key material; upstream UUIDs are credentials and remain root-only.
6. NRM only imports native TCP + REALITY VLESS links. Unsupported transports are rejected rather than silently converted.
7. UDP is rejected for traffic entering the NRM VLESS inbound. VLESS upstream outbounds are configured with `network: tcp` and empty `packet_encoding`.
8. DoH uses HTTPS over TCP. The host operating system and package manager may still use UDP for unrelated services; use host/provider firewall policy if a machine-wide UDP prohibition is required.
9. Review `install.sh` before running it as root. It installs the official sing-box APT repository and downloads the NRM source archive from the configured GitHub repository.
10. A `GOMEMLIMIT` value is a Go runtime soft memory target, not a hard process or cgroup memory cap.
