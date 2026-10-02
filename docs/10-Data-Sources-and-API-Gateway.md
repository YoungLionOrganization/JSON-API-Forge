# Data Sources and API Gateway

Data sources expose controlled non-resource data. Supported patterns include static values, local JSON/YAML/CSV files and outbound HTTP.

File-backed writable sources are useful for small single-host data but are not a concurrent distributed database. Writes use file locking/atomic replacement patterns where implemented. Do not use them for high-write multi-host state.

HTTP sources must use operator-configured destinations. Redirects are disabled in the resilient client; response bytes can be bounded; retries are method-aware. Non-idempotent mutation retries require explicit opt-in and an upstream idempotency contract. Network-level firewall/egress policy remains recommended because application URL checks cannot replace infrastructure controls.
