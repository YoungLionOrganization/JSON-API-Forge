# Platform and Hosting Matrix

JSON API Forge v0.5.1 has one application contract across desktop and server environments. The safest deployment choice is native ASGI behind TLS. Compiled artifacts are conveniences, not substitutes for testing against the exact host kernel, libc, proxy and database drivers.

## Delivery matrix

| Host family | v0.5.1 artifact | Architectures | Recommended runtime | Important boundary |
|---|---|---:|---|---|
| Ubuntu, Debian, RHEL, Rocky, AlmaLinux, Fedora, Arch, openSUSE and compatible glibc Linux | `linux-glibc-*` | x64, ARM64 | `forge-server` or Python/Uvicorn | Build on the oldest supported glibc baseline; use the Python wheel when a vendor kernel/libc is incompatible. |
| Alpine, postmarketOS and other musl Linux | `linux-musl-*` | x64, ARM64 | musl-native `forge-server` | Do not mix glibc and musl executables. |
| Windows 10/11 and Windows Server | `windows-x64` | x64; x64 compatibility on ARM64 | `forge-server.exe`, optionally a Windows service | Native Windows ARM64 lacks required binary wheels for the frozen dependency graph; the installer deliberately selects the verified x64 compatibility build. Terminate TLS in IIS, Caddy, nginx or another trusted reverse proxy. |
| macOS | `macos-*` | x64, ARM64 | `forge-server` for development/small private hosts | Release signing/notarization is separate from compilation. |
| Docker, Kubernetes, Podman | OCI tar or source `Dockerfile` | x64, ARM64 | Uvicorn in a non-root container | Mount data read/write paths explicitly; put secrets in the platform secret store. |
| cPanel / Passenger | cPanel source bundle or universal wheel | Provider-dependent | Passenger/a2wsgi for HTTP | WSGI does not support the Editor call WebSocket; use native ASGI for realtime. |
| Other Unix/BSD, ARMv7, exotic NAS/router firmware | universal wheel/source | Platform-dependent | Python 3.10-3.14 if dependencies support the host | No matching frozen binary is claimed; compile/test locally. |

The GitHub workflow creates checksums beside every archive. `scripts/install-release.sh` and `scripts/install-release.ps1` select only known asset names, download over HTTPS, validate SHA-256, and refuse an existing destination. Windows ARM64 is detected locally and mapped to the x64 compatibility asset; no unsupported native ARM64 server binary is advertised. The installers never fetch or execute a platform value supplied by a remote response.

## Secure remote Editor topology

1. Bind Forge to loopback or a private interface. Keep `forge-server`'s default `127.0.0.1` unless a reverse proxy or private network requires another address.
2. Terminate TLS at a maintained reverse proxy. Forward only the real client/protocol headers and add that proxy's exact CIDR to `EDITOR_TRUSTED_PROXY_CIDRS`.
3. Restrict `EDITOR_ALLOWED_IPS` and `EDITOR_TRUSTED_HOSTS`; add VPN/firewall enforcement. Application API keys do not authenticate the Editor control plane.
4. Run `forge init --editor --production`, create the founder exactly once, set `EDITOR_SETUP_ENABLED=false`, and remove `EDITOR_TOKEN` from the environment.
5. Invite each worker into explicit project roles. Use document and database scopes for sensitive projects; reserve hook editing for fully trusted ranks.
6. Use PostgreSQL/MySQL for concurrent production state, `forge migrate`, then `INTERNAL_SCHEMA_MODE=validate`. Back up the internal database because it contains accounts, memberships, notes and audit state.
7. Configure organization-controlled STUN/TURN with `EDITOR_CALL_ICE_SERVERS_JSON`. TURN credentials reach authorized call participants' browsers, so prefer short-lived credentials and restrict relay policy.

The server never accepts raw SQL through the Editor. Database browsing is bounded `SELECT` over runtime-declared tables and hides resource `hidden_fields`; undeclared tables require both a server switch and an explicit permission.

## Server templates

- `deploy/systemd/json-api-forge.service` binds to loopback and applies systemd hardening. Create the `forge` user and deployment directories before enabling it.
- `deploy/nginx/editor-control-plane.conf.example` shows TLS, request limits and WebSocket upgrade handling. Replace every placeholder and use the real proxy/client CIDRs.
- `deploy/windows/install-service.ps1` registers an existing verified `forge-server.exe` as a Windows service; the chosen service account still needs least-privilege ACLs for the deployment data paths.
- `deploy/cpanel/.htaccess.example` is a provider-dependent Passenger starting point. Keep application files outside `public_html` whenever the provider permits it.

Specialized Windows editions, hardened Linux distributions, SELinux/AppArmor policies and managed panels can deny filesystem, network, camera/signaling or process capabilities independently of Forge. Treat the matrix as build coverage and a deployment recipe, then run `forge doctor --production`, `forge validate`, a database migration rehearsal and an Editor login/call smoke test on the actual target.
