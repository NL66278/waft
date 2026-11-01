#!/usr/bin/env python3
"""Create an expense declaration in Odoo via the OpenEyeDev admin API.

Gathers user input and POSTs to the /openeyedev/v1/declare controller (in the
openeyedev_admin_api module), which does the real work server-side:

  1. Creates an hr.expense for the employee (default from
     ODOO_DECLARATION_EMPLOYEE_ID) with the given product (category), the
     actual vendor, amount, date, description.
  2. Attaches the --doc file (if any) to the expense as its main attachment.
  3. Submits, approves, and posts the expense. Posting is overridden so the
     vendor bill is raised to the actual vendor (DR <cost account> / CR
     130000 Creditors, partner=vendor).
  4. Creates a MISC journal entry reclassing 130000 -> 053100 (partner=
     employee) and reconciles the two 130000 lines, settling the vendor
     payable.

Net booking: DR <cost account> / CR 053100 Vorderingen op participanten
(partner=employee), with the vendor recorded on the expense and the vendor
bill, and a reclass entry documenting the move from 130000 to 053100.

Dry-run by default; --confirm is required to actually write.

Usage:
  # Dry-run (default — validates server-side and prints the planned moves):
  python3 scripts/declare.py \\
      --date 2026-11-01 --amount 42.50 --product FOOD \\
      --vendor "Cortecs GmbH" --description "LLM API credits"

  # Actually create (with an attachment):
  python3 scripts/declare.py \\
      --date 2026-11-01 --amount 42.50 --product FOOD \\
      --vendor "Cortecs GmbH" --description "LLM API credits" \\
      --doc receipt.pdf --confirm

Configuration (in .env-secret, gitignored; read via os.environ):
  ODOO_RPC_BASE                e.g. https://odoo.openeyedev.eu
  ODOO_RPC_DB                  e.g. odooedprd19
  ODOO_RPC_LOGIN               e.g. opencode@openeyedev.eu
  ODOO_APIKEY_PASS_PATH        pass entry holding the API key
  ODOO_DECLARATION_EMPLOYEE_ID employee id to create the expense for (e.g. 2)

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

ROUTE = "/openeyedev/v1/declare"


def _env(key, default=None):
    return os.environ.get(key, default)


def _read_doc_b64(path):
    p = Path(path)
    if not p.is_file():
        raise OdooError(f"Attachment not found: {path}")
    data = base64.b64encode(p.read_bytes()).decode()
    mime, _ = mimetypes.guess_type(str(p))
    return data, p.name, mime or "application/octet-stream"


def _print_preview(r):
    print("\n=== Dry-run preview (nothing will be written) ===")
    print(f"  Date        : {r['date']}")
    print(f"  Amount      : {r['amount']:,.2f}")
    print(f"  Description : {r['description']!r}")
    p = r["product"]
    print(f"  Product     : {p['name']} (code={p['default_code'] or '—'})")
    print(f"  Vendor      : {r['vendor']['name']} (id {r['vendor']['id']})")
    e = r["employee"]
    print(f"  Employee    : {e['name']} (id {e['id']})")
    ca = r["cost_account"]
    print(f"  Cost account: {ca['code']} {ca['name']}")
    if r.get("doc"):
        print(f"  Doc         : {r['doc']}")
    print("\n  Planned vendor bill (Purchases/BILL, in_receipt):")
    for ln in r["planned_bill"]["lines"]:
        _print_line(ln)
    print("\n  Planned MISC reclass (Misc. Operations, entry):")
    for ln in r["planned_reclass"]["lines"]:
        _print_line(ln)
    print(f"  Reconcile   : {r['planned_reclass']['reconcile']}")
    print("\nDry-run only. Re-run with --confirm to create the expense + booking.")


def _print_line(ln):
    if "DR" in ln:
        side, amt = "DR", ln["DR"]
    else:
        side, amt = "CR", ln["CR"]
    print(f"    {side}  {amt:>10,.2f}  {ln['account']:<8}  (partner: {ln['partner']})")


def _print_result(r):
    print("\n=== Created ===")
    print(f"  Expense    : id={r['expense_id']}  {r['expense_name']!r}")
    print(f"  Vendor bill: id={r['bill_id']}  {r['bill_name']!r}")
    print(f"  Reclass    : id={r['reclass_id']}  {r['reclass_name']!r}")
    print(f"  Amount     : {r['amount']:,.2f}")
    print(f"  Vendor     : {r['vendor']}")
    print(f"  Employee   : {r['employee']}")
    print("\nDone. Review the entries in Odoo.")


def main():
    ap = argparse.ArgumentParser(
        prog="declare.py",
        description="Create an expense declaration in Odoo via the admin API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--date", help="Expense date (YYYY-MM-DD)")
    ap.add_argument("--amount", type=float, help="Amount to declare (positive)")
    ap.add_argument(
        "--product",
        help="Product/category code, name, or id (e.g. FOOD, 'Meals', 3)",
    )
    ap.add_argument(
        "--vendor",
        help="Vendor partner name or id (e.g. 'Cortecs GmbH', 29)",
    )
    ap.add_argument("--description", help="Expense description")
    ap.add_argument("--doc", help="Path to a file to attach as documentation")
    ap.add_argument(
        "--confirm",
        action="store_true",
        help="Actually create (default: dry-run)",
    )
    args = ap.parse_args()

    missing = [
        n
        for n in ("date", "amount", "product", "vendor", "description")
        if getattr(args, n) is None
    ]
    if missing:
        ap.error(
            f"missing required argument(s): {', '.join('--' + m for m in missing)}"
        )
    if args.amount <= 0:
        ap.error("--amount must be a positive number")

    params = {
        "date": args.date,
        "amount": args.amount,
        "product": args.product,
        "vendor": args.vendor,
        "description": args.description,
        "dry_run": not args.confirm,
    }
    emp_id = _env("ODOO_DECLARATION_EMPLOYEE_ID")
    if emp_id:
        params["employee_id"] = int(emp_id)
    if args.doc:
        data, name, mime = _read_doc_b64(args.doc)
        params["doc_base64"] = data
        params["doc_name"] = name
        params["doc_mime"] = mime

    client = OdooClient.connect()
    try:
        result = client.call_route(ROUTE, params)
    except OdooError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if result.get("dry_run"):
        _print_preview(result)
    else:
        _print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
