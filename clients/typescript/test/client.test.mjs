import assert from "node:assert/strict";
import test from "node:test";

import { ForgeAPIError, ForgeClient } from "../.test-dist/index.js";

test("base URL policy rejects insecure or ambiguous origins", () => {
  assert.throws(() => new ForgeClient({ baseUrl: "http://api.example.com" }), /HTTPS/);
  assert.throws(
    () => new ForgeClient({ baseUrl: "http://api.example.com", allowHttpForLoopback: true }),
    /HTTPS/,
  );
  assert.doesNotThrow(
    () => new ForgeClient({ baseUrl: "http://127.0.0.1:8000", allowHttpForLoopback: true }),
  );
  assert.throws(() => new ForgeClient({ baseUrl: "https://api.example.com/base/../admin" }), /unsafe/);
  assert.throws(() => new ForgeClient({ baseUrl: "https://user:secret@api.example.com" }), /baseUrl/);
  assert.throws(() => new ForgeClient({ baseUrl: "https://api.example.com\n.attacker.invalid" }), /unsafe/);
  assert.throws(() => new ForgeClient({ baseUrl: "https://api.example.com/%5cadmin" }), /encoded/);
  assert.throws(() => new ForgeClient({ baseUrl: "https://api.example.com", timeoutMs: Number.NaN }), /safe integer/);
  assert.throws(() => new ForgeClient({ baseUrl: "https://api.example.com", maxResponseBytes: Number.POSITIVE_INFINITY }), /safe integer/);
  assert.throws(() => new ForgeClient({ baseUrl: "https://api.example.com", apiKey: "safe\u0000unsafe" }), /control/);
});

test("request remains under the configured base and encodes identifiers", async () => {
  const seen = [];
  const client = new ForgeClient({
    baseUrl: "https://api.example.com/api/task-board/v1",
    apiKey: "test-key",
    fetchImpl: async (url, init) => {
      seen.push({ url: String(url), init });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  assert.deepEqual(await client.get("tasks", "item 1"), { ok: true });
  assert.equal(seen[0].url, "https://api.example.com/api/task-board/v1/tasks/item%201");
  assert.equal(seen[0].init.redirect, "manual");
  assert.equal(new Headers(seen[0].init.headers).get("X-API-Key"), "test-key");
  await assert.rejects(() => client.request("GET", "https://attacker.invalid/collect"), /origin-relative/);
  await assert.rejects(() => client.request("GET", "/%00hidden"), /encoded/);
  await assert.rejects(() => client.request("GET\r\nInjected", "/health"), /HTTP token/);
});

test("root base URL, redirects, and response limits are handled safely", async () => {
  const responses = [
    new Response("", { status: 302, headers: { location: "https://attacker.invalid/collect" } }),
    new Response("x".repeat(1025), { status: 200 }),
  ];
  const client = new ForgeClient({
    baseUrl: "https://api.example.com",
    maxResponseBytes: 1024,
    fetchImpl: async () => responses.shift(),
  });

  await assert.rejects(() => client.request("GET", "/health"), ForgeAPIError);
  await assert.rejects(
    () => client.request("GET", "/health"),
    (error) => error instanceof ForgeAPIError && error.payload === "response exceeded the configured size limit",
  );
});
