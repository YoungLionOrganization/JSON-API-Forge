# JSON API Forge Editor v0.5.2 release checklist

## Source

- [ ] The branch contains Editor source/assets/packaging only.
- [ ] CMake, VERSION, README, EDITOR and release metadata identify 0.5.2.
- [ ] The full icon and transparent in-app mark render correctly.
- [ ] `python scripts/check_manifest.py` passes.

## Security and behavior

- [ ] Session and call-ticket formats reject suffixes/control bytes.
- [ ] Redirect, proxy, TLS and loopback-only HTTP tests pass.
- [ ] Passwords/setup tokens/sessions are never persisted.
- [ ] Plugin ZIP traversal, symlink, collision, size/ratio, CRC, digest, API and explicit-enable checks pass.
- [ ] Remote roles, database browsing, file sharing and calls are verified
      against a hardened v0.5.2 server.

## Build and packaging

- [ ] Warnings-as-errors CMake build and CTest pass on all six targets.
- [ ] Packaged-app screenshot smoke tests pass.
- [ ] Every portable ZIP contains more than 20 deployed files.
- [ ] Official Qt IFW tool downloads match the pinned SHA-256 values.
- [ ] Linux Qt IFW RUN installers are executable and contain the deployed staging tree.
- [ ] Windows Qt IFW EXE installers are created from the deployed staging tree.
- [ ] macOS Qt IFW DMGs contain the staged app and pass `hdiutil verify`.
- [ ] Every Qt IFW installer presents and installs the repository license.
- [ ] Every SHA-256 sidecar verifies in the combined job.
- [ ] Editor CodeQL is green.

## Publish

- [ ] Download and inspect the Actions artifacts.
- [ ] Complete platform signing/notarization where required.
- [ ] Attach portable ZIPs, installers and checksums to v0.5.2.
