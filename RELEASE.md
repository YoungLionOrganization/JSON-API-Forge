# JSON API Forge v0.5.2

**Status: publication candidate; publication is a separate manual action.** This release covers the canonical server distribution from `main`. Editor, Python SDK and example applications retain their independent branches, versions and release ownership.

## Release summary

v0.5.2 fixes resource-cache isolation, heterogeneous batch inserts, ISO datetime coercion, nullable cursor pagination, PUT defaults, BIGINT declarations, media cleanup and malformed/non-finite input handling. It also improves actual endpoint cancellation, JWKS refresh/client reuse, DNS isolation, readiness concurrency and startup schema/client overhead.

Server delivery uses a manual publisher with successful exact-commit workflow gates, artifact provenance, archive integrity checks, complete companion checksums, resumable draft uploads and remote digest verification. The public release body is maintained in [`release/notes/v0.5.2.md`](release/notes/v0.5.2.md).

## Compatibility and upgrade review

- Existing JSON configuration remains accepted. HTTP source `total_timeout_seconds` is optional and defaults to `null`; existing per-phase timeout/retry behavior is retained unless a total budget is explicitly selected.
- Request timeout before response headers now cancels the actual async endpoint and waits for cooperative cleanup. Streaming after headers retains its existing lifetime semantics. Running synchronous hooks and some DB/DNS work cannot be forcibly stopped by an async deadline.
- JWKS refresh throttling defaults to 5 seconds. A newly rotated, previously unknown key may wait up to that interval; expired keys are not used as an outage fallback.
- Nullable secondary SQL cursors now sort nulls first consistently. Start new pagination sessions after upgrading rather than carrying old cursor tokens across versions.
- PUT resets omitted nonnullable fields with scalar/server defaults; callable defaults require an explicit value.
- PostgreSQL BIGINT declarations are corrected for newly created tables. Existing physical columns are unchanged and require an explicit migration if their type must change. Auto-create remains schema creation, not a migration framework.
- Existing Passenger bridges and deployed custom `forge_wsgi.py` files are not automatically replaced. No worker is started at bridge construction or before the first worker request.

Review application-specific hooks, replacement writes and cursor clients before deployment. Preserve the prior package, application configuration and databases for rollback; reverting the runtime does not revert separately applied database migrations.

## Validation evidence and remaining checks

Latest local validation on Python 3.12:

| Check | Result |
|---|---|
| Python tests | 166 passed, 7 external-service tests skipped |
| Branch-aware coverage | 81.35%; critical module floors passed |
| Ruff lint/format | Passed |
| Source manifest | Passed; regenerate and verify for the final commit after documentation changes |
| Wheel/sdist, Twine, dependency consistency | Passed |
| TypeScript reference client | Type checking and 3 tests passed during v0.5.2 preparation |

Controlled latency probes used 10 local cold starts and 300 warm requests. The 10-project Passenger first-request median changed from 143 ms to 36 ms; warm medians remained around 1.2 ms. These compare the initial v0.5.2 development implementation with the local latency patch, not a deployed v0.5.1-to-v0.5.2 production upgrade. They exclude hosting worker creation, real DNS/TLS and remote DB latency. See the [analysis and reproduction commands](docs/performance/v0.5.2-latency-analysis.md).

GitHub's Python matrix, live PostgreSQL/Redis/MongoDB, container, CodeQL and standalone platform checks must still succeed. The publisher itself has local tests; actual draft creation, remote uploads and public publication require their own Actions execution. A green PR run is review evidence, not the required `main` push run after merge.

## Prepare the reviewed main commit

1. Review and merge the server PR into `main`.
2. Check that `VERSION`, `pyproject.toml`, TypeScript reference-client metadata, release metadata and expected asset filenames agree on 0.5.2. Regenerate `MANIFEST.sha256` whenever tracked source or documentation changes.
3. Wait for these workflows on the **same full main commit SHA**, using their latest successful run/attempt:

   | Metadata entry | Required workflow |
   |---|---|
   | `ci.yml` | CI, including its required Python/package/live-service/TypeScript/container jobs |
   | `codeql.yml` | CodeQL |
   | `server-builds.yml` | Server and portable platform builds |

4. Confirm that the publisher and rerun actor are listed in [`release/authorized_publishers.json`](release/authorized_publishers.json). Both actors are checked.

The build exports Linux glibc/musl, Windows, macOS, OCI, wheel/source and cPanel/Passenger packages listed in [`release/assets.json`](release/assets.json). Platform support and architecture coverage remain defined by the [hosting matrix](docs/43-Platform-and-Hosting-Matrix.md) and build matrix; Windows x64 compatibility is not a native Windows ARM64 package.

## Validate → publish all components

Open **Actions → Validate or publish all components → Run workflow** on `main`. Choose `component: all` and leave `source_sha` empty. `mode: validate` verifies all four prepared drafts; `mode: publish` publishes Main, Editor, Python SDK and example applications in the same workflow run. See [PUBLISH.md](PUBLISH.md) for the complete procedure.

The reviewed [component plan](release/components-v0.5.2.json) pins each branch's source SHA, exact successful build/CodeQL attempts, release ID and complete asset hashes. The workflow downloads small hash-verified build proofs; it reuses the already verified draft assets. It checks every component before making a public transition and requires successful CI/CodeQL on the publisher controller commit itself.

One component can also be selected. The optional source field is a consistency check for the prepared candidate; in `all` mode it applies only to Main. Surrounding clipboard whitespace and uppercase hex are normalized. A mismatching source is refused rather than silently publishing a different build.

The legacy server-only command remains available for preparing an initial server draft: `python scripts/publish_release.py --sha SHA --mode draft`. All four v0.5.2 drafts are already prepared and verified. The combined workflow publishes them under their independent tags.

## Retry and recovery

A failed upload leaves a private incomplete draft. Retrying verifies the digest of every existing asset and uploads only missing files. Conflicting, duplicated or unexpected assets stop the operation for maintainer review; the script does not clobber them. The combined publisher accepts an already public component only when its prepared source, tag and every asset digest still match; it leaves that component unchanged and continues with remaining drafts. The legacy server-only publisher still refuses public releases.

Expired artifacts require a new successful platform run and fresh validation. A failed or in-progress latest attempt cannot fall back to an older success. A changed build/attempt requires package verification and an updated component plan. If a required workflow changes or reruns during publication, validation must be repeated.

[`release/release.json`](release/release.json) declares the version, component, workflow gates and artifact. Build identity is stored in `build.json` and carried into the release as `release-build.json`; this records the source SHA, build run ID, run attempt, version and verified asset hashes. The publisher reuses built files and does not rebuild binaries during publication.

Use the updated [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) for the server review. Historical v0.5.1 handoff notes remain historical and are not this version's publication instructions.

## Editor call compatibility

The current server advertises call-client revision 2. Upgrade and restart the
running server together with Editor v0.5.2. The previous “Waiting for secure
authorization…” page was served by older main builds. The new page uses an
independent startup fallback and a bounded authorization timeout, and reports
expired tickets or failed signaling connections visibly. Call pages remain
uncached, tickets remain one-time and the nonce-based content security policy
applies to both scripts. Browser tests cover two-participant audio/video,
concurrent joins, denied media, missing/reused tickets, stalled authorization
and a broken primary script.

GitHub publishes separate releases through separate API calls; the four public transitions are not an atomic transaction. On an interrupted run, inspect the saved publication report and rerun the combined workflow to reconcile already published components and finish the remaining drafts.
