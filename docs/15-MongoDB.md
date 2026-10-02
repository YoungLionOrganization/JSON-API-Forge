# MongoDB

MongoDB integration uses PyMongo's asynchronous client. Configure named databases and declarative collection resources with allowed actions, filters, sorts and write fields.

Tenant, owner and soft-delete fields are policy fields. They are injected/preserved by the server and must not be exposed as client-writable fields. v0.4.1 fixes the bundled Mongo example accordingly.

Owner actions can restrict list/read/update/delete to the authenticated subject with an optional explicit bypass permission. Tenant-bound resources require a principal tenant. IDs accept ObjectId-shaped strings or native string IDs.

Use a real Mongo service integration test for connection/pool/CRUD behavior; unit mocks are not sufficient for release confidence.
