# Third-party notices

[LICENSE](LICENSE) (`LicenseRef-JAF-SASH-1.1`) applies to original JSON API Forge material. Section 7 preserves third-party terms and rights. Dependency versions are resolved per platform; this document identifies the main components and the release process, not an assertion that every resolved dependency has one license.

## Runtime and tooling

| Component group | Upstream source / license record |
|---|---|
| Python runtime | [CPython license and incorporated components](https://docs.python.org/3/license.html) |
| API/ASGI and models | [FastAPI](https://github.com/fastapi/fastapi), [Starlette](https://github.com/Kludex/starlette), [Uvicorn](https://github.com/encode/uvicorn), [Pydantic](https://github.com/pydantic/pydantic) |
| SQL and drivers | [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy), [asyncpg](https://github.com/MagicStack/asyncpg), [asyncmy](https://github.com/long2ice/asyncmy), [aiosqlite](https://github.com/omnilib/aiosqlite) |
| MongoDB and Redis clients | [PyMongo](https://github.com/mongodb/mongo-python-driver), [redis-py](https://github.com/redis/redis-py) |
| HTTP and cryptography | [HTTPX](https://github.com/encode/httpx), [httpcore](https://github.com/encode/httpcore), [PyJWT](https://github.com/jpadilla/pyjwt), [cryptography](https://github.com/pyca/cryptography) |
| Parsing and schemas | [orjson](https://github.com/ijl/orjson), [PyYAML](https://github.com/yaml/pyyaml), [jsonschema](https://github.com/python-jsonschema/jsonschema) |
| Reference TypeScript tooling | [TypeScript](https://github.com/microsoft/TypeScript); see `clients/typescript/package.json` |
| Frozen executable builder | [PyInstaller](https://github.com/pyinstaller/pyinstaller), including its bootloader exception and bundled components |

The complete direct dependency declarations are in [pyproject.toml](pyproject.toml). Their transitives, Python's bundled libraries and native components also need review when shipped. Database/Redis server services are separate products; the client's license does not describe the server's license.

For an installed environment, `python -m pip inspect > dependency-environment.json` records resolved distributions and metadata. Use the relevant environment rather than a developer environment with unrelated packages. Preserve dependency license files from each distribution's `.dist-info/licenses` or equivalent location alongside that record. Frozen/OCI builds include more than the project's wheel; inspect those payloads separately.

Official standalone packaging carries the project license, FAQ, notices and supporting policies under `share/json-api-forge/`. Python wheels/sdists carry the declared license files in their package metadata. These documents do not replace upstream notices for native or frozen dependencies.

## Release and provenance records

Keep the exact source commit, dependency versions, upstream license texts/copyright notices and any local patches for the artifact being published. A permissive top-level package can contain subcomponents under other terms. Do not infer the license from a package name or an automated badge; use its installed metadata and corresponding upstream source.

Original project redistribution remains governed by LICENSE. Third-party licenses can impose separate notice, source or other obligations for components included in a binary, image or installer. A successful build does not by itself establish compliance. Review the exact payload before an official release and preserve required notices when repackaging it.
