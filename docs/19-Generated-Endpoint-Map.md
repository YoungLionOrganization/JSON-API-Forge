# Generated Endpoint Map

Project endpoints are rooted at each `api_prefix`. Typical generated shapes are:

- `GET/POST <prefix>/<resource>` and `GET/PATCH/PUT/DELETE <prefix>/<resource>/{item_id}`
- `GET <prefix>/<resource>/_count`
- `<method> <prefix>/<operation.path>`
- `<prefix>/<data-source.path>`
- `<prefix>/<event.path>`, `/stream`, `/ws`
- `<prefix>/media`, `/media/_batch`, `/media/{id}`, `/meta`, `/signed-url`
- `<prefix>/admin/api-keys`, `/admin/jwt`
- `<prefix>/meta`, `/_openapi.json`, `/_docs`, `/_redoc`

Exact routes depend on actions/features enabled in configuration. Use `forge routes` and project OpenAPI rather than relying on a static document for deployment tooling.
