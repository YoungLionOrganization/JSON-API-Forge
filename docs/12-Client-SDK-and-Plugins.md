# Client SDK and Plugins

The official typed Python SDK lives only on the `python-library` branch and is
built as the `json-api-forge-client` package. The main branch keeps the small
`clients/typescript` reference client for its runtime contract tests; it does
not duplicate the Python SDK.

Keep privileged API keys on trusted server-side clients. Browser/mobile environments that cannot protect static credentials should normally authenticate users with short-lived identity tokens and expose only the permissions needed by that client.

Install the Python SDK from its branch during development:

```bash
python -m pip install "json-api-forge-client @ git+https://github.com/YoungLionOrganization/JSON-API-Forge.git@python-library"
```

The branch workflow builds a universal wheel and source distribution for
later PyPI publication. Keep privileged API keys and Editor sessions only in
trusted clients.
