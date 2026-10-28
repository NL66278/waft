# TOOLS.md — Odoo 19 workspace (Waft install)

Repo-specific notes for the Open Eye Development Odoo 19 workspace. For
general infrastructure notes (SSH, email, web, OpenCode harness), see the
top-level `~/TOOLS.md`.

This file is committed to the repo. **Do not put secrets, `pass` paths, or
personal details here.** Secrets and the `pass` entry path live in
`.env-secret` (gitignored) and are read by scripts via `os.environ`.

## Odoo production instance

- **Instance:** `https://odoo.openeyedev.eu` (⚠️ note: `.eu`, not `.com`)
- **Version:** Odoo 19.0 (final)
- **Database:** `odooedprd19`
- **Purpose:** Administratie, CRM, project management
- **Source:** https://github.com/odoo/odoo
- **Type:** Eigen self-hosted instance
- **Local dev:** this workspace, database `odooeddem19`,
  `http://127.0.0.1:8069`

## Programmatic API access

Configuration is via environment variables, sourced from `.env-secret`
(gitignored — never committed). The committed code reads them with
`os.environ`; nothing secret lives in this file.

| Variable | Purpose | Example |
|----------|---------|---------|
| `ODOO_RPC_BASE` | Server base URL | `https://odoo.openeyedev.eu` |
| `ODOO_RPC_DB` | Production database name | `odooedprd19` |
| `ODOO_RPC_LOGIN` | Login (API key owner) | `opencode@openeyedev.eu` |
| `ODOO_APIKEY_PASS_PATH` | `pass` entry holding the API key | (personal — in `.env-secret`) |
| `ODOO_APIKEY` | (Alternative) set the key directly | (personal — in `.env-secret` if used) |

### ⚠️ Auth method matters in Odoo 19

API keys are **not** validated by `/web/session/authenticate`. That endpoint
is *interactive* and only checks the user's real password; an API key is
silently ignored → `Access Denied`. This is the #1 footgun when scripting
Odoo.

- ❌ Do **not** send an API key as the `password` to
  `/web/session/authenticate`.
- ✅ Correct: `POST /jsonrpc` with `service=common, method=authenticate`,
  passing the API key as the password arg. Returns the `uid`. Then use
  `service=object, method=execute_kw` with
  `[db, uid, api_key, model, method, args, kwargs]`.
- API keys are scope-bound (scope `'rpc'`) and only validated on
  **non-interactive** RPC calls.
- Standard Odoo 19 API key format: 40 hex chars
  (`binascii.hexlify(os.urandom(20))`), **no** dashes. (The 43-char dashed
  format comes from the OCA `auth_api_key` module, not standard Odoo.)

### Client script — `scripts/odoo_client.py`

The supported way to do programmatic Odoo RPC in this repo.

- **CLI self-test:** `python3 scripts/odoo_client.py`
  (add `--quiet` to skip the self-test and only authenticate)
- **Library:**

  ```python
  from odoo_client import OdooClient
  client = OdooClient.connect()  # reads .env-secret + gets key via pass
  partners = client.search_read(
      "res.partner", [["is_company", "=", True]], ["id", "name"]
  )
  client.write("res.partner", [id], {"name": "..."})  # live prod data
  ```

- **Wrappers:** `search`, `search_count`, `search_read`, `read`,
  `create`, `write`, `unlink`, plus the generic
  `execute(model, method, args, kwargs)`.
- Loads `.env-default` / `.env-shared` / `.env-secret` from the repo root
  (won't overwrite already-exported vars). Uses `python-dotenv` if
  available, otherwise a minimal inline parser.
