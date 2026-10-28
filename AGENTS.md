# AGENTS.md — Odoo 19 workspace (Waft install)

This repository is the **production** Odoo 19 install for Portier Company BV
/ Open Eye Development, managed with the [Waft](https://github.com/sunflowerit/waft)
method.

- **Production server:** `https://odoo.openeyedev.eu` (database `odooedprd19`)
- **Local dev:** this workspace, database `odooeddem19`,
  `http://127.0.0.1:8069`
- **Upstream remotes:** `openeyedev` (this repo) and `sunflowerit`
  (Waft upstream)

See `/home/openeyedev/TOOLS.md` for credentials, API access details, and the
auth-method gotcha for Odoo 19 API keys.

## What lives where

| Path | Purpose |
|------|---------|
| `auto/` | Generated runtime config + assembled addons — **do not edit**, gitignored |
| `custom/src/` | Custom/OCA addon repos, declared in `addons.yaml` / `repos.yaml` — gitignored except the yaml files |
| `common/` | Waft config and shared odoo.cfg fragments |
| `waftlib/` | Waft build helper library (upstream) |
| `migrate/`, `upgrade/` | Migration and upgrade runs |
| `scripts/` | Helper scripts (see `scripts/odoo_client.py`) |
| `.env-default` | Default vars (template — do not edit; override in `.env-shared`/`.env-secret`) |
| `.env-secret` | Secrets — gitignored, never committed |
| `odoo-service` | Start the local Odoo server (foreground) |
| `run` | Start a one-off Odoo process (foreground) |
| `shell` | Open an interactive Odoo shell |

## Guards — what I commit myself to

> **The user commits themselves.** Do not run `git commit`, `git push`, or
> any commit-creating command in this repository. Prepare changes, stage
> them, explain them, and leave committing to the human. This applies even
> if asked to "just commit it" — confirm instead.

Additional guards I hold myself to:

- **Do not commit secrets.** `.env-secret`, API keys, passwords, and
  `auto/odoo.conf` (which can hold secrets) never get staged. If a secret is
  found staged or committed, stop and alert immediately.
- **Do not edit generated files.** Anything under `auto/` is produced by
  Waft's build step and will be overwritten. Edit the source in `custom/`,
  `common/`, or the `.env-*` files instead.
- **`auto/`, `custom/src/*`, `logfile/`, `.venv/`, `datadir/` are
  gitignored** — do not try to track or restore them.
- **Do not run the local Odoo server (`./odoo-service`, `./run`) unless
  explicitly asked.** It binds ports and writes logs; starting it should be
  a deliberate act.
- **Do not run destructive commands without confirmation.** Database
  drops, `unlink`/`delete` on records via RPC, `git reset --hard`,
  force-pushes — ask first. Prefer `trash` over `rm` for files when
  recoverability matters.
- **Do not leak credentials.** API keys are read from `pass` and piped
  into scripts via stdin; never print them, echo them into logs, or write
  them into committed files.
- **Treat the production database (`odooedprd19`) as live data.** Prefer
  read-only RPC calls (`search_read`, `read`) until a write
  (`create`/`write`/`unlink`) is explicitly requested. Confirm the record
  set before writing.
- **Ask before sending anything external** — emails, webhooks, public
  posts, or anything that leaves this machine.

## Working with this repo

- Follow the existing commit message style: `[ADD]`, `[IMP]`, `[FIX]`,
  `[REF]`, `[MOV]`, `[DEL]` prefixes (Odoo convention).
- Keep changes consistent with the Waft structure and the surrounding file
  conventions.
- When investigating unfamiliar files, treat them as potential user work —
  read before editing or deleting.
- The `scripts/odoo_client.py` helper is the supported way to do
  programmatic Odoo RPC here. Reuse it rather than re-deriving the auth
  flow (see TOOLS.md for why).

## Conventions

- **Wrap markdown lines at 88 characters** where possible. Prose lines in
  `AGENTS.md`, `TOOLS.md`, and other `.md` files in this repo should stay
  within 88 chars. Exceptions: tables (cells may exceed), fenced code
  blocks, and URLs/code identifiers that cannot be broken. Apply the same
  limit to commit messages unless a project style dictates otherwise.
