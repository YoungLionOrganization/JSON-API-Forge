# Reverse Proxy Trust, TLS and Client IP

The direct socket peer is authoritative unless it belongs to `trusted_proxy_cidrs`. Only then does Forge walk `X-Forwarded-For` from the nearest proxy backward to find the effective untrusted client address. Forwarded protocol is similarly trusted only from configured proxies.

Configure host allowlists and TLS requirements per project. Do not set broad trusted proxy networks just to “make the real IP work”; that converts spoofable client headers into security input.

Official Uvicorn commands disable independent proxy-header rewriting so this trust logic remains consistent. If your hosting platform rewrites client/scheme values before ASGI, understand that platform behavior and test it explicitly.
