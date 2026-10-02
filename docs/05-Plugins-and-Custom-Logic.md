# Plugins and Custom Logic

Python hooks are the escape hatch when declarative primitives stop being the clearest representation of business behavior. Keep hooks project scoped, importable and explicit.

Custom endpoints can define method/path, permission/public status, input mode/schema, parameters, dependencies, response kind, OpenAPI metadata and background hooks. Generic framework configuration should not become a hidden programming language; if behavior requires multiple conditions, third-party SDK calls or domain algorithms, use a hook/service.

Background hooks execute after the main response lifecycle according to FastAPI background task semantics. v0.4.1 specifically prevents idempotent replay from scheduling an operation's background hooks again; the replay represents the original completed operation, not a new side effect.
