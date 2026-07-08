# Tenant Migration: int identifiers → UUID

This is the one-time runbook a **tenant site** runs to move off integer
identifiers and onto UUIDs. Every tenant must complete it before Aegis ships
**phase 2 (contract)**, which drops the integer `id`/foreign-key columns. After
phase 2 the API no longer accepts integer `site_id`/`user_id`, and the two Aegis
installs (`aegis.mazza.vc`, `aegis.reallybadapps.com`) can be merged.

> Validated by three completed migrations: the DevNotes pilot (2026-06-25), the
> Eterna/Arcana migration (2026-07-05), and api-gatekeeper (2026-07-08). The notes
> below incorporate all three tenants' feedback — client versions, the webhook
> path, and which step actually gates phase 2 (DevNotes); npm lockfile drift,
> `set -e` build-script hardening, dry-run schema guards, and confirming your real
> deploy host (Arcana/Eterna); and the fail-closed-guard lockout corner case +
> UUID case-normalization (api-gatekeeper).

## Why this is needed

UUIDs are globally unique; integer ids are not. When the two installs are
merged, integer ids collide across them — only the UUIDs survive. So anything a
tenant has **configured** (`AEGIS_SITE_ID`) or **stored** (e.g. an
`aegis_id`/`aegis_user_id` column on its own rows) must be re-pointed at the UUID.

## ⚠️ Timing constraint — do this while the dual-support shim is live

Aegis currently accepts **both** integer and UUID identifiers and returns
**both** on every response. That window is the only time you can map an old
integer to its UUID. **Once phase 2 drops the integer column, the mapping is
gone — you cannot recover it.** Complete every step below before phase 2.

This migration is **safe and reversible** during the shim: nothing is dropped,
the integer ids keep working, so you can flip `AEGIS_SITE_ID` and re-key your
data incrementally and roll back if needed.

## Prerequisites — check before you start

- **Client library version floor.** This migration requires
  `byteforge-aegis-client-python >= 1.7.0` and `byteforge-aegis-models >= 1.4.0`
  (or `byteforge-aegis-client-js >= 2.12.0`). Earlier clients type `site_id` as
  `Optional[int]`, so the Step 4 UUID flip breaks them immediately.
- **Bust your pip/npm cache.** If you pin the client to a branch (e.g. `@main`),
  pip's layer cache can silently serve the old version. Rebuild with
  `pip install --no-cache-dir ...` (or bump the pin) and confirm the installed
  version meets the floor above.
- **JS/TS tenants — regenerate your lockfile *inside* the build image.** npm 10
  and npm 11 resolve the client dependency to different `package-lock.json`
  shapes. If your build host runs a different npm major than your Docker image,
  the lockfile drifts and the build can silently ship a stale client that
  doesn't meet the floor above. Regenerate the lock (`npm install`) inside the
  build image, not on the host, so the resolved version is the one that ships.
  *(Reported by the Arcana/Eterna migration.)*
- **🛑 STOP — before you rebuild anything, verify your build script has `set -e`.**
  A `build-publish.sh` (or equivalent) *without* `set -e` keeps running after a
  failed `docker build` — it pushes a stale `:latest` and bumps your VERSION,
  shipping the OLD image under a NEW tag, and hands you a baffling rollback. This
  is the single highest-leverage check in this runbook: two of three tenants so
  far had a build script missing `set -e`. If this migration rebuilds your tenant
  image, confirm the script fails fast (`set -e` or explicit exit-code checks)
  **first**. *(Reported by the Arcana/Eterna and api-gatekeeper migrations.)*
- **Audit numeric coercion of `AEGIS_SITE_ID`.** Search your tenant code for
  `int(AEGIS_SITE_ID)` or any cast/`parseInt` on the site id and **remove it** —
  the client accepts both forms now, and the cast throws the instant the env var
  becomes a UUID string.

---

## Step 1 — Get your site's UUID

The by-domain lookup is public (no API key) and already returns `uuid`.

**curl**
```bash
curl "https://<aegis-host>/api/sites/by-domain?domain=<your-domain>"
# → { "id": 2, "uuid": "ff100a68-5ce5-41ab-9c2c-10ac194dedb4", "name": "...", ... }
```

**Python client**
```python
site = client.get_site_by_domain("your-domain.com")
print(site.id, site.uuid)
```

**JS/TS client**
```ts
const res = await client.getSiteByDomain("your-domain.com");
console.log(res.data.id, res.data.uuid);
```

Record the `uuid` — it becomes your new `AEGIS_SITE_ID` in Step 4.

## Step 2 — Map each stored user id to its UUID

For every `aegis_user_id` your app has stored, look up the user and read back the
`uuid`. **Prefer the client library** — `User.uuid` is right there on the
response, with no shell-quoting or JSON-parsing. The endpoint is gated by your
tenant API key (`X-Tenant-Api-Key`) and scoped to your own site; it accepts the
id as an **integer or a UUID**.

**Python client** (configured with `tenant_api_key` + `site_id`)
```python
for old_id in stored_user_ids:
    user = client.get_user(old_id)     # → GET /api/sites/<site_id>/users/<old_id>
    print(old_id, "→", user.uuid)
```

**JS/TS client**
```ts
for (const oldId of storedUserIds) {
  const res = await client.getUser(oldId, siteId);
  console.log(oldId, "→", res.data.uuid);
}
```

**curl** (fine for a handful of users)
```bash
curl "https://<aegis-host>/api/sites/<site_id>/users/<user_id>" \
  -H "X-Tenant-Api-Key: <your-tenant-api-key>"
# → { "id": 42, "uuid": "9b2c...", "site_id": 2, "site_uuid": "ff10...", ... }
```

A `401` means the id is unknown **or** belongs to another site (the response is
deliberately uniform to avoid cross-tenant probing). If you get a `401` for an
id you believe is yours, confirm you're querying the right install and site.

## Step 3 — Re-key your local data

In your own database, replace each stored integer `aegis_user_id` with the UUID
from Step 2. Recommended approach:

1. Add an `aegis_uuid` column (`UUID UNIQUE`) alongside the existing integer column.
2. Backfill it using the Step 2 mapping.
3. Cut your code over to read/write the UUID column. A safe transition pattern is
   **UUID-first with INT fallback**, where the fallback fires *only* when the
   matched row's `aegis_uuid IS NULL` (fail-closed on inconsistency).
4. Drop the old integer column **only after** the order-of-operations footnote
   below is satisfied.

> **Footnote — get the fail-closed guard right, or you'll lock out a real user.**
> "Fallback fires only when `aegis_uuid IS NULL`" is not the whole rule. The
> subtle case: an incoming token whose `user.uuid` is **absent** (a pre-shim
> Aegis response, or a client library that predates the field) hits a row that
> *has* been backfilled. A naive `row.aegis_uuid != incoming_uuid` then compares
> `<uuid> != None` → `True` → you refuse a legitimate user. **Correct rule:**
> refuse *only* when BOTH sides carry a UUID **and** they disagree. If the
> incoming token has no UUID, accept the INT match unconditionally — you have
> *less* information, not *conflicting* information. Also **normalize UUID case on
> both sides** before comparing (`str(uuid.UUID(v))`): Postgres canonicalizes
> `UUID` columns to lowercase, so an uppercase form from any middleware fails a
> case-sensitive string compare and rejects a valid match. *(Both reported by the
> api-gatekeeper migration — their code review caught the lockout before deploy.)*

> **Footnote — when it's safe to drop the integer column.** If you build a
> UUID-first / INT-fallback path, you must NOT drop the integer column until BOTH
> (a) no row has `aegis_uuid IS NULL`, AND (b) you have removed the fallback code
> path. Dropping it early locks out any user the backfill missed. Most tenants
> should leave the integer column in place until Aegis phase 2 lands server-side.

> **Footnote — if your migration script has a `--dry-run`, guard reads on the
> new column.** The `aegis_uuid` column does not exist until the `ALTER` runs, so
> a dry-run that queries it (or `SELECT ... FROM information_schema` /
> `column_name = 'aegis_uuid'`) before the ALTER will throw or report misleading
> state on a first pass. Make the dry-run path defensively check the column's
> existence (or skip the read) so it's safe to run *before* the schema change.
> *(Reported by the Arcana/Eterna migration.)*

## Step 3.5 — If you consume Aegis webhooks

Aegis webhook payloads now carry **both** `user_id` (int) and `user_uuid`
(UUID) — and `site_id`/`site_uuid`. Update your webhook handler to read
`payload['user_uuid']` and persist it on create, and to inline-backfill
`aegis_uuid` on an existing row when a webhook arrives carrying one. The field is
`Optional` during the shim (fall back to `NULL` if absent).

**Why this matters:** if you skip it, accounts created *after* your cutover are
born with `aegis_uuid = NULL`, which keeps the legacy INT-fallback path
load-bearing forever and silently blocks you from ever dropping the integer
column.

## Step 4 — Flip `AEGIS_SITE_ID`  *(REQUIRED before phase 2)*

Change the env var from the integer to the UUID from Step 1:

```diff
- AEGIS_SITE_ID=2
+ AEGIS_SITE_ID=ff100a68-5ce5-41ab-9c2c-10ac194dedb4
```

Restart the tenant app.

> **This step — not the backfill — is the phase-2 readiness signal.** During the
> shim it's functionally a no-op (Aegis accepts both forms), so it's tempting to
> defer. But your outbound proxy calls keep sending the **integer** `site_id` in
> request bodies until you flip it, and those calls break the moment Aegis drops
> integer acceptance. Backfill (Steps 2–3) is safe to do early; **the env flip is
> the thing Aegis is waiting on** to know your tenant is ready. Do not treat it as
> indefinitely optional.

## Step 5 — Verify

- **DB invariant (run this):**
  ```sql
  SELECT COUNT(*) FROM users WHERE aegis_id IS NOT NULL AND aegis_uuid IS NULL;
  -- expect 0
  ```
  This catches the "a user is silently still on the INT fallback" failure mode
  that an end-user login check would miss.
- A user can log in and reach authenticated routes.
- Any server-to-server call your app makes to Aegis (e.g. `get_user`) still
  succeeds with the UUID-based config.
- Your app's own user-owned data resolves correctly via the new UUID key.

If anything misbehaves, revert `AEGIS_SITE_ID` to the integer — the shim still
accepts it — and investigate.

---

## Operational note — admin-agent escalation

**Confirm your actual deploy host and its escalation path first.** Hosts differ
per tenant (apollo, brutus, heimdall, zeus, …) — some have a resident admin
agent, some route deploys straight to a human. Do **not** assume the migration
ticket named the right host or admin agent; verify where *your* app actually
deploys before you start, so you know who runs the DB write and the env flip.

If your host is managed by an automation agent (e.g. an apollo/bragi-style
admin agent), expect the **DB-write and env-edit steps to escalate to a human**
for approval even when the migration ticket pre-authorizes them. Plan for at
least one human-in-the-loop pause per tenant deploy, and note that an approval
scoped to "the DB write" will **not** cover the Step 4 env flip — that's a
separate change request.

## Completion checklist (per tenant, per install)

| Tenant site | Install | Step 1 site UUID | Step 2 user UUIDs | Step 3 re-keyed | Step 3.5 webhook | Step 4 `AEGIS_SITE_ID` flipped | Step 5 verified |
|-------------|---------|:---:|:---:|:---:|:---:|:---:|:---:|
|             |         |     |     |     |     |     |     |

Phase 2 is gated on **every** row above being complete across **both** installs —
and specifically on the **Step 4 env flip**, which is the readiness signal.
