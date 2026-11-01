#!/usr/bin/env python3
"""Create an expense declaration (journal entry) in Odoo.

Books a declaration as a DRAFT account.move with two balanced lines:

  DR <cost account>                                <amount>   (expense)
  CR 053100 Vorderingen op participanten           <amount>   (company owes you)

The move is created in state='draft' so you can review it in Odoo and post
manually. Nothing is written unless --confirm is passed; by default the
script prints what it would create (dry-run).

A one-time --setup creates the Declarations journal.

Usage:
  # Dry-run (default — writes nothing, prints the planned move):
  python3 scripts/declare.py \\
      --date 2026-10-28 --amount 42.50 --account 433000 \\
      --description "Office supplies"

  # Actually create the draft move (requires --confirm):
  python3 scripts/declare.py \\
      --date 2026-10-28 --amount 42.50 --account 433000 \\
      --description "Office supplies" --confirm

  # With a document attachment (PDF, image, etc.):
  python3 scripts/declare.py \\
      --date 2026-10-28 --amount 42.50 --account 433000 \\
      --description "Office supplies" --doc receipt.pdf --confirm

  # One-time: create the Declarations journal:
  python3 scripts/declare.py --setup --confirm

Configuration (in .env-secret, gitignored; read via os.environ):
  ODOO_COUNTER_ACCOUNT_CODE        default "053100"
  ODOO_DECLARATION_PARTNER_ID      participant partner id (e.g. 6)
  ODOO_DECLARATION_JOURNAL_CODE    default "DECL"

See repo AGENTS.md for guards — prod data, confirm before writes.
"""

import argparse
import base64
import mimetypes
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from odoo_client import OdooClient, OdooError  # noqa: E402

# --- env helpers ------------------------------------------------------------


def _env(key, default=None):
    return os.environ.get(key, default)


def _load_env():
    """odoo_client._load_env_files already loads .env-* on OdooClient.connect()."""
    pass


# --- setup: create the declarations journal ---------------------------------


def setup_journal(client: OdooClient, code: str, name: str = "Declaraties"):
    """Create a general-type journal for declarations, if it does not exist."""
    existing = client.search_read(
        "account.journal",
        [["code", "=", code]],
        ["id", "name", "code"],
        limit=1,
    )
    if existing:
        print(
            f"Journal {code!r} already exists: id={existing[0]['id']} "
            f"name={existing[0]['name']!r} — nothing to do."
        )
        return existing[0]["id"]
    company = client.read("res.users", [client.uid], ["company_id"])[0]["company_id"]
    jid = client.create(
        "account.journal",
        {
            "name": name,
            "code": code,
            "type": "general",
            "company_id": company[0],
        },
    )
    print(f"Created journal {code!r} ({name!r}): id={jid}")
    return jid


# --- resolve account / partner / journal ------------------------------------


def resolve_account(client: OdooClient, code: str, *, must_be_expense: bool = False):
    """Find an account.account by exact code. Returns (id, code, name, type)."""
    rows = client.search_read(
        "account.account",
        [["code", "=", code]],
        ["id", "code", "name", "account_type"],
        limit=1,
    )
    if not rows:
        raise OdooError(f"No account found with code {code!r}")
    a = rows[0]
    if must_be_expense and not a["account_type"].startswith("expense"):
        raise OdooError(
            f"Account {code} ({a['name']!r}) is of type {a['account_type']!r}, "
            f"not an expense account. Use an expense account for the cost side."
        )
    return a["id"], a["code"], a["name"], a["account_type"]


def resolve_journal(client: OdooClient, code: str):
    rows = client.search_read(
        "account.journal",
        [["code", "=", code]],
        ["id", "name", "code", "type"],
        limit=1,
    )
    if not rows:
        raise OdooError(
            f"Journal {code!r} not found. Run "
            f"`python3 {Path(__file__).name} --setup --confirm` to create it."
        )
    return rows[0]["id"], rows[0]["name"], rows[0]["code"]


def resolve_partner(client: OdooClient, partner_id: int):
    rows = client.read("res.partner", [partner_id], ["id", "name", "email"])
    if not rows:
        raise OdooError(f"Partner id {partner_id} not found")
    return rows[0]["id"], rows[0]["name"]


# --- file attachment --------------------------------------------------------


def attach_document(client: OdooClient, move_id: int, file_path: str):
    p = Path(file_path)
    if not p.is_file():
        raise OdooError(f"Attachment not found: {file_path}")
    data = base64.b64encode(p.read_bytes()).decode()
    mime, _ = mimetypes.guess_type(str(p))
    att_id = client.create(
        "ir.attachment",
        {
            "name": p.name,
            "res_model": "account.move",
            "res_id": move_id,
            "datas": data,
            "mimetype": mime or "application/octet-stream",
            "type": "binary",
        },
    )
    return att_id, p.name


# --- build the move ---------------------------------------------------------


def build_move_vals(
    journal_id, date, ref, cost_account_id, counter_account_id, amount, partner_id
):
    """Two-line balanced entry: DR cost / CR 053100 (participant receivable)."""
    return {
        "journal_id": journal_id,
        "date": date,
        "ref": ref,
        "move_type": "entry",
        "line_ids": [
            (
                0,
                0,
                {
                    "name": ref,
                    "account_id": cost_account_id,
                    "debit": amount,
                    "credit": 0.0,
                    "partner_id": False,
                    "date": date,
                },
            ),
            (
                0,
                0,
                {
                    "name": ref,
                    "account_id": counter_account_id,
                    "debit": 0.0,
                    "credit": amount,
                    "partner_id": partner_id,
                    "date": date,
                },
            ),
        ],
    }


def fmt_amount(x):
    return f"{x:,.2f}"


def print_move_preview(vals, cost_acct, counter_acct, partner_name, journal_name):
    print("\n=== Planned move (DRY-RUN — nothing will be written) ===")
    print(f"  Journal : {journal_name}")
    print(f"  Date    : {vals['date']}")
    print(f"  Ref     : {vals['ref']!r}")
    print(f"  Type    : {vals['move_type']}  (state=draft on creation)")
    print("  Lines:")
    dr = vals["line_ids"][0][2]
    cr = vals["line_ids"][1][2]
    print(f"    DR  {fmt_amount(dr['debit']):>10}  {cost_acct[1]:<8} {cost_acct[2]}")
    print(
        f"    CR  {fmt_amount(cr['credit']):>10}  {counter_acct[1]:<8} "
        f"{counter_acct[2]}  (partner: {partner_name})"
    )
    print(
        f"  Balance : DR {fmt_amount(dr['debit'])}  ==  CR {fmt_amount(cr['credit'])}  "
        f"-> balanced"
        if dr["debit"] == cr["credit"]
        else "  -> UNBALANCED"
    )
    print()


# --- main -------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        prog="declare.py",
        description="Create an expense declaration (draft journal entry) in Odoo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--date", help="Expense date (YYYY-MM-DD)")
    ap.add_argument("--amount", type=float, help="Amount to declare (positive)")
    ap.add_argument("--account", help="Cost account code (e.g. 433000)")
    ap.add_argument("--description", help="Description / reference for the move")
    ap.add_argument("--doc", help="Path to a file to attach as documentation")
    ap.add_argument(
        "--confirm",
        action="store_true",
        help="Actually create the move (default: dry-run)",
    )
    ap.add_argument(
        "--setup",
        action="store_true",
        help="Create the Declarations journal (one-time)",
    )
    args = ap.parse_args()

    # --- setup mode ---
    if args.setup:
        if not args.confirm:
            print("Setup requires --confirm to actually create the journal.")
            print("Run: python3 scripts/declare.py --setup --confirm")
            return 1
        client = OdooClient.connect()
        code = _env("ODOO_DECLARATION_JOURNAL_CODE", "DECL")
        setup_journal(client, code)
        return 0

    # --- declare mode: validate args ---
    missing = [
        n
        for n in ("date", "amount", "account", "description")
        if getattr(args, n) is None
    ]
    if missing:
        ap.error(
            f"missing required argument(s): {', '.join('--' + m for m in missing)}"
        )
    if args.amount <= 0:
        ap.error("--amount must be a positive number")

    # --- connect + resolve ---
    client = OdooClient.connect()

    counter_code = _env("ODOO_COUNTER_ACCOUNT_CODE", "053100")
    partner_id_env = _env("ODOO_DECLARATION_PARTNER_ID")
    journal_code = _env("ODOO_DECLARATION_JOURNAL_CODE", "DECL")

    if not partner_id_env:
        print(
            "ERROR: ODOO_DECLARATION_PARTNER_ID not set. Add it to .env-secret "
            "(the participant partner id, e.g. 6).",
            file=sys.stderr,
        )
        return 1

    cost_acct = resolve_account(client, args.account, must_be_expense=True)
    counter_acct = resolve_account(client, counter_code)
    journal = resolve_journal(client, journal_code)
    partner_id, partner_name = resolve_partner(client, int(partner_id_env))

    vals = build_move_vals(
        journal_id=journal[0],
        date=args.date,
        ref=args.description,
        cost_account_id=cost_acct[0],
        counter_account_id=counter_acct[0],
        amount=args.amount,
        partner_id=partner_id,
    )

    # --- dry-run preview ---
    print_move_preview(
        vals, cost_acct, counter_acct, partner_name, f"{journal[2]} {journal[1]}"
    )
    if args.doc:
        print(f"  Doc     : would attach {args.doc!r}")
        if not Path(args.doc).is_file():
            print(f"  WARNING: attachment {args.doc!r} does not exist locally")
    print()

    if not args.confirm:
        print("Dry-run only. Re-run with --confirm to create this draft move.")
        return 0

    # --- create ---
    print("Creating draft move ...")
    move_id = client.create("account.move", vals)
    print(f"Created account.move id={move_id}")

    # Read back the assigned name (Odoo may set a draft name)
    mv = client.read(
        "account.move",
        [move_id],
        ["name", "date", "ref", "state", "journal_id", "amount_total"],
    )[0]
    print(f"  name   : {mv.get('name')!r}")
    print(f"  date   : {mv['date']}")
    print(f"  ref    : {mv.get('ref')!r}")
    print(f"  state  : {mv['state']}")
    print(f"  total  : {mv['amount_total']}")
    print(f"  URL    : {client.base}/odoo/account/{move_id}")

    if args.doc:
        print(f"Attaching {args.doc!r} ...")
        att_id, fname = attach_document(client, move_id, args.doc)
        print(f"  attached ir.attachment id={att_id} ({fname})")

    print("\nDone. Review and post the move in Odoo when ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
