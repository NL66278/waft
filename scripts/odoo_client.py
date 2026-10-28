#!/usr/bin/env python3
"""Odoo 19 JSON-RPC client using an API key (scope 'rpc').

Configuration is read from environment variables (see .env-secret in the repo,
which is gitignored and never committed):
  ODOO_RPC_BASE             e.g. https://odoo.openeyedev.eu
  ODOO_RPC_DB               e.g. odooedprd19
  ODOO_RPC_LOGIN            e.g. opencode@openeyedev.eu
  ODOO_APIKEY_PASS_PATH     pass entry holding the API key (set in .env-secret)

The pass path and the API key itself never live in this committed file. The
script loads .env-secret from the repo root (if present) so it works when run
standalone; if the env vars are already exported (shell sourced the .env-*),
those take precedence (os.environ wins over the file fallback).

Auth model (the gotcha — see repo TOOLS.md):
  Odoo 19 API keys are NOT validated by /web/session/authenticate (that
  endpoint is interactive and only checks the user's real password). Instead,
  POST /jsonrpc with service=common, method=authenticate, passing the API key
  as the password arg. Returns the uid. Then call model methods via
  service=object, method=execute_kw with [db, uid, api_key, model, method, args, kwargs].

Usage as a library:
    from odoo_client import OdooClient
    client = OdooClient.connect()          # reads env + gets key from pass
    partners = client.search_read("res.partner", [["is_company", "=", True]], ["id", "name"])

Usage from CLI:
    python3 scripts/odoo_client.py           # self-test (auth + read own user + partner count)
    python3 scripts/odoo_client.py --quiet   # authenticate only, print uid
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


class OdooError(RuntimeError):
    pass


# --- .env loading -----------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILES = [_REPO_ROOT / ".env-default", _REPO_ROOT / ".env-shared", _REPO_ROOT / ".env-secret"]


def _load_env_files() -> None:
    """Load .env-default/.env-shared/.env-secret (in that order) WITHOUT
    overwriting variables already present in os.environ, so a shell that
    already sourced the files wins."""
    try:
        from dotenv import dotenv_values  # type: ignore
    except ImportError:
        # Minimal fallback parser: KEY=VALUE per line, strips comments/quotes.
        def dotenv_values(path: Path):
            vals = {}
            if not path.is_file():
                return vals
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if v and len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
                    v = v[1:-1]
                vals[k] = v
            return vals

    for path in _ENV_FILES:
        for k, v in dotenv_values(path).items():
            os.environ.setdefault(k, v)


# --- API key retrieval ------------------------------------------------------

def _api_key_from_pass(pass_path: str) -> str:
    """Read the API key from the given `pass` entry (first line only)."""
    try:
        out = subprocess.run(
            ["pass", pass_path], check=True, capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        raise OdooError("`pass` binary not found; install pass or set ODOO_APIKEY to override")
    except subprocess.CalledProcessError as e:
        raise OdooError(f"`pass {pass_path}` failed: {e.stderr.strip() or e}") from None
    key = out.stdout.splitlines()[0].strip() if out.stdout else ""
    if not key:
        raise OdooError(f"`pass {pass_path}` returned an empty key")
    return key


def _get_api_key() -> str:
    """Resolve the API key from ODOO_APIKEY (direct) or via pass using
    ODOO_APIKEY_PASS_PATH."""
    direct = os.environ.get("ODOO_APIKEY")
    if direct:
        return direct
    pass_path = os.environ.get("ODOO_APIKEY_PASS_PATH")
    if not pass_path:
        raise OdooError(
            "No API key configured. Set ODOO_APIKEY (direct) or "
            "ODOO_APIKEY_PASS_PATH (pass entry) in .env-secret."
        )
    return _api_key_from_pass(pass_path)


# --- client -----------------------------------------------------------------

class OdooClient:
    """Stateless JSON-RPC client for Odoo 19 using an API key."""

    def __init__(self, base: str, db: str, uid: int, api_key: str, timeout: int = 30):
        self.base = base.rstrip("/")
        self.db = db
        self.uid = uid
        self.api_key = api_key
        self.timeout = timeout
        self._id = 0

    @classmethod
    def connect(cls, *, timeout: int = 30) -> "OdooClient":
        """Build a ready-to-use client from environment configuration.

        Reads ODOO_RPC_BASE, ODOO_RPC_DB, ODOO_RPC_LOGIN and an API key
        (from ODOO_APIKEY or ODOO_APIKEY_PASS_PATH). Loads .env-* files from
        the repo root first so the script works standalone.
        """
        _load_env_files()
        base = os.environ.get("ODOO_RPC_BASE")
        db = os.environ.get("ODOO_RPC_DB")
        login = os.environ.get("ODOO_RPC_LOGIN")
        if not (base and db and login):
            raise OdooError(
                "Missing RPC config. Set ODOO_RPC_BASE, ODOO_RPC_DB, ODOO_RPC_LOGIN "
                "in .env-secret."
            )
        api_key = _get_api_key()
        return cls.from_apikey(db, login, api_key, base=base, timeout=timeout)

    @classmethod
    def from_apikey(cls, db: str, login: str, api_key: str, *, base: str, timeout: int = 30) -> "OdooClient":
        """Authenticate with an API key and return a ready-to-use client."""
        tmp = cls(base, db, 0, api_key, timeout=timeout)
        res = tmp._call("/jsonrpc", {
            "service": "common",
            "method": "authenticate",
            "args": [db, login, api_key, {}],
        })
        uid = res.get("result")
        if not isinstance(uid, int) or not uid:
            raise OdooError(f"Authentication failed: {res}")
        return cls(base, db, uid, api_key, timeout=timeout)

    def _call(self, endpoint: str, params: dict) -> dict:
        self._id += 1
        payload = {"jsonrpc": "2.0", "method": "call", "id": self._id, "params": params}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + endpoint, data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:500]
            raise OdooError(f"HTTP {e.code} from {endpoint}: {body}") from None

    def execute(self, model: str, method: str, args: list | None = None, kwargs: dict | None = None):
        """Call `execute_kw` on a model method. Raises OdooError on server error."""
        res = self._call("/jsonrpc", {
            "service": "object",
            "method": "execute_kw",
            "args": [self.db, self.uid, self.api_key, model, method, args or [], kwargs or {}],
        })
        if res.get("error"):
            err = res["error"]
            raise OdooError(err.get("data", {}).get("message") or err.get("message") or err)
        return res.get("result")

    # Convenience wrappers ---------------------------------------------------

    def search(self, model, domain, *, limit=None, offset=0, order=None):
        kwargs = {}
        if limit is not None: kwargs["limit"] = limit
        if offset: kwargs["offset"] = offset
        if order: kwargs["order"] = order
        return self.execute(model, "search", args=[domain], kwargs=kwargs)

    def search_count(self, model, domain):
        return self.execute(model, "search_count", args=[domain])

    def search_read(self, model, domain, fields=None, *, limit=None, offset=0, order=None):
        kwargs = {}
        if limit is not None: kwargs["limit"] = limit
        if offset: kwargs["offset"] = offset
        if order: kwargs["order"] = order
        return self.execute(model, "search_read", args=[domain, fields or []], kwargs=kwargs)

    def read(self, model, ids, fields=None):
        return self.execute(model, "read", args=[ids, fields or []])

    def create(self, model, values):
        return self.execute(model, "create", args=[values])

    def write(self, model, ids, values):
        return self.execute(model, "write", args=[ids, values])

    def unlink(self, model, ids):
        return self.execute(model, "unlink", args=[ids])


# --- CLI / self-test --------------------------------------------------------

def _self_test(client: OdooClient) -> None:
    me = client.read("res.users", [client.uid], ["id", "login", "name", "email"])
    print(f"Authenticated: uid={client.uid} (db={client.db})")
    print("My user record:", me)
    count = client.search_count("res.partner", [])
    print(f"res.partner count: {count}")
    mods = client.search_read(
        "ir.module.module", [("state", "=", "installed")], ["name", "state"], limit=5,
    )
    print("Sample installed modules:", mods)


def main() -> int:
    quiet = "--quiet" in sys.argv
    try:
        client = OdooClient.connect()
    except OdooError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if quiet:
        print(f"Authenticated: uid={client.uid}")
    else:
        _self_test(client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
