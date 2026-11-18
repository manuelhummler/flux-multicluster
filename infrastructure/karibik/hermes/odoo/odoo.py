#!/usr/bin/env python3
"""odoo: Odoo (Online, beg-paf.odoo.com) fuer Hermes ueber die externe XML-RPC-API.

Generische Befehle (search/read/create/write/delete/call) fuer jedes Modell plus Abkuerzungen fuer Kontakte,
CRM, Verkauf, Rechnungen, Einkauf, Projekte und Zeiterfassung. Zugang per API-Key eines eigenen Odoo-Benutzers;
die Zugangsdaten liegen in ~/.config/odoo/credentials.json (0600, schreibt der Init-Container install-extras aus
dem Secret hermes-secrets) oder in ODOO_URL/ODOO_DB/ODOO_USER/ODOO_API_KEY. Ausgabe immer JSON.

Schutz: `delete` und jeder zustandsaendernde `call` (Buchen, Bestaetigen, Stornieren, ...) verlangen `--yes`.
Nur Standardbibliothek (xmlrpc.client), keine Zusatzpakete.

Quelle: infrastructure/karibik/hermes/odoo/odoo.py im Flux-Repo (ConfigMap odoo-skill), der Init-Container
install-extras kopiert sie nach skills/business/odoo/scripts/. Doku 4e "Odoo".
"""
import argparse
import datetime as dt
import json
import os
import sys
import xmlrpc.client

CRED_FILES = [
    os.path.expanduser("~/.config/odoo/credentials.json"),
    "/opt/data/home/.config/odoo/credentials.json",
]
# Methoden, die ohne --yes erlaubt sind (lesend / rechnend). Alles andere gilt als Zustandsaenderung.
READ_PREFIXES = ("search", "read", "name_", "fields_", "get_", "check_", "default_get", "web_", "onchange",
                 "export_", "load_", "copy_data", "action_get")
DEFAULT_LIMIT = 50


def out(data, code=0):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    sys.exit(code)


def fail(msg, **extra):
    out({"error": msg, **extra}, 1)


# --- Verbindung ------------------------------------------------------------------------------------------

class Odoo:
    def __init__(self):
        cfg = {}
        for path in CRED_FILES:
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        cfg = json.load(f)
                except (OSError, ValueError) as exc:
                    fail(f"Zugangsdaten {path} nicht lesbar: {exc}")
                break
        self.url = (os.environ.get("ODOO_URL") or cfg.get("url") or "").rstrip("/")
        self.db = os.environ.get("ODOO_DB") or cfg.get("db") or ""
        self.user = os.environ.get("ODOO_USER") or cfg.get("user") or ""
        self.key = os.environ.get("ODOO_API_KEY") or cfg.get("api_key") or ""
        if not all((self.url, self.db, self.user, self.key)):
            fail("Zugangsdaten fehlen (ODOO_URL, ODOO_DB, ODOO_USER, ODOO_API_KEY bzw. ~/.config/odoo/credentials.json). "
                 "Den Betreiber bitten, hermes-secrets zu pruefen.")
        # /odoo am Ende der Web-URL gehoert nicht zur API-Basis
        if self.url.endswith("/odoo"):
            self.url = self.url[:-5]
        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        self._uid = None

    @property
    def uid(self):
        if self._uid is None:
            try:
                self._uid = self.common.authenticate(self.db, self.user, self.key, {})
            except Exception as exc:
                fail(f"Anmeldung fehlgeschlagen: {_err(exc)}")
            if not self._uid:
                fail("Anmeldung abgelehnt (Benutzer, Datenbank oder API-Key falsch).")
        return self._uid

    def call(self, model, method, args=None, kwargs=None):
        try:
            return self.models.execute_kw(self.db, self.uid, self.key, model, method, args or [], kwargs or {})
        except Exception as exc:
            fail(f"Odoo-Fehler bei {model}.{method}: {_err(exc)}")

    def search_read(self, model, domain, fields=None, limit=DEFAULT_LIMIT, order=None, offset=0):
        kw = {"limit": limit, "offset": offset}
        if fields:
            kw["fields"] = fields
        if order:
            kw["order"] = order
        return self.call(model, "search_read", [domain], kw)


def _err(exc):
    if isinstance(exc, xmlrpc.client.Fault):
        text = exc.faultString or ""
        # Odoo haengt den Server-Traceback an; nur die letzte aussagekraeftige Zeile behalten
        lines = [l for l in text.strip().splitlines() if l.strip()]
        return lines[-1][:500] if lines else text[:500]
    return str(exc)[:500]


def _json_arg(text, what):
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        fail(f"{what} ist kein gueltiges JSON: {exc}")


def _ids(values):
    try:
        return [int(v) for v in values]
    except ValueError:
        fail("IDs muessen Ganzzahlen sein.")


def _fields(text):
    return [f.strip() for f in text.split(",") if f.strip()] if text else None


def _today():
    return dt.date.today().isoformat()


# --- Generische Befehle --------------------------------------------------------------------------------------

def cmd_version(o, a):
    out({"url": o.url, "db": o.db, "server": o.common.version()})


def cmd_me(o, a):
    u = o.call("res.users", "read", [[o.uid]], {"fields": ["name", "login", "company_id", "tz", "lang"]})[0]
    out({"uid": o.uid, "name": u["name"], "login": u["login"], "company": u["company_id"], "tz": u.get("tz"),
         "lang": u.get("lang")})


def cmd_models(o, a):
    domain = [["model", "ilike", a.filter]] if a.filter else []
    if not a.all:
        domain.append(["transient", "=", False])
    rows = o.search_read("ir.model", domain, ["model", "name"], limit=a.limit, order="model")
    out(rows)


def cmd_fields(o, a):
    attrs = ["string", "type", "relation", "required", "readonly", "selection", "help"]
    fields = o.call(a.model, "fields_get", [], {"attributes": attrs})
    items = []
    for name, meta in sorted(fields.items()):
        if a.filter and a.filter.lower() not in name.lower() and a.filter.lower() not in (meta.get("string") or "").lower():
            continue
        if not a.help:
            meta.pop("help", None)
        items.append({"name": name, **meta})
    out(items)


def cmd_search(o, a):
    domain = _json_arg(a.domain, "--domain") or []
    out(o.search_read(a.model, domain, _fields(a.fields), a.limit, a.order, a.offset))


def cmd_count(o, a):
    domain = _json_arg(a.domain, "--domain") or []
    out({"model": a.model, "count": o.call(a.model, "search_count", [domain])})


def cmd_read(o, a):
    kw = {"fields": _fields(a.fields)} if a.fields else {}
    out(o.call(a.model, "read", [_ids(a.ids)], kw))


def cmd_create(o, a):
    values = _json_arg(a.values, "--values")
    if not isinstance(values, dict):
        fail("--values muss ein JSON-Objekt sein.")
    new_id = o.call(a.model, "create", [values])
    out({"model": a.model, "id": new_id, "url": f"{o.url}/odoo/{a.model}/{new_id}"})


def cmd_write(o, a):
    values = _json_arg(a.values, "--values")
    if not isinstance(values, dict):
        fail("--values muss ein JSON-Objekt sein.")
    ids = _ids(a.ids)
    o.call(a.model, "write", [ids, values])
    out({"model": a.model, "ids": ids, "written": sorted(values)})


def cmd_delete(o, a):
    if not a.yes:
        fail("Loeschen verlangt --yes. Vorher dem Nutzer nennen, was geloescht wird (read).")
    ids = _ids(a.ids)
    o.call(a.model, "unlink", [ids])
    out({"model": a.model, "deleted": ids})


def cmd_call(o, a):
    args = _json_arg(a.args, "--args") or []
    kwargs = _json_arg(a.kwargs, "--kwargs") or {}
    if a.ids:
        args = [_ids(a.ids)] + list(args)
    if not a.method.startswith(READ_PREFIXES) and not a.yes:
        fail(f"{a.model}.{a.method} aendert vermutlich Daten (buchen, bestaetigen, stornieren, ...) und verlangt --yes. "
             "Vorher dem Nutzer sagen, was passiert.")
    out({"model": a.model, "method": a.method, "result": o.call(a.model, a.method, args, kwargs)})


# --- Abkuerzungen ----------------------------------------------------------------------------------------------

def cmd_contacts(o, a):
    domain = ["|", "|", ["name", "ilike", a.query], ["email", "ilike", a.query], ["phone", "ilike", a.query]]
    if a.companies:
        domain = ["&", ["is_company", "=", True]] + domain
    fields = ["name", "is_company", "parent_id", "email", "phone", "mobile", "city", "country_id", "function",
              "customer_rank", "supplier_rank"]
    out(o.search_read("res.partner", domain, fields, a.limit, "name"))


def cmd_leads(o, a):
    domain = []
    if a.query:
        domain += ["|", ["name", "ilike", a.query], ["partner_id", "ilike", a.query]]
    if a.stage:
        domain.append(["stage_id", "ilike", a.stage])
    if not a.all:
        domain.append(["active", "=", True])
    fields = ["name", "type", "partner_id", "contact_name", "email_from", "stage_id", "expected_revenue",
              "probability", "user_id", "date_deadline", "create_date"]
    out(o.search_read("crm.lead", domain, fields, a.limit, "create_date desc"))


def cmd_sales(o, a):
    domain = []
    if a.state:
        domain.append(["state", "=", a.state])
    if a.partner:
        domain.append(["partner_id", "ilike", a.partner])
    fields = ["name", "partner_id", "date_order", "state", "amount_untaxed", "amount_total", "invoice_status",
              "user_id", "validity_date"]
    out(o.search_read("sale.order", domain, fields, a.limit, "date_order desc"))


def cmd_invoices(o, a):
    move_type = {"out": "out_invoice", "in": "in_invoice", "out_refund": "out_refund", "in_refund": "in_refund"}[a.type]
    domain = [["move_type", "=", move_type]]
    if a.state:
        domain.append(["state", "=", a.state])
    if a.unpaid:
        domain += [["state", "=", "posted"], ["payment_state", "in", ["not_paid", "partial"]]]
    if a.partner:
        domain.append(["partner_id", "ilike", a.partner])
    fields = ["name", "partner_id", "invoice_date", "invoice_date_due", "state", "payment_state",
              "amount_untaxed", "amount_total", "amount_residual", "ref", "invoice_origin"]
    out(o.search_read("account.move", domain, fields, a.limit, "invoice_date desc, name desc"))


def cmd_purchases(o, a):
    domain = []
    if a.state:
        domain.append(["state", "=", a.state])
    if a.partner:
        domain.append(["partner_id", "ilike", a.partner])
    fields = ["name", "partner_id", "date_order", "state", "amount_untaxed", "amount_total", "invoice_status",
              "date_planned", "user_id"]
    out(o.search_read("purchase.order", domain, fields, a.limit, "date_order desc"))


def cmd_projects(o, a):
    domain = [["active", "=", True]] if not a.all else []
    fields = ["name", "partner_id", "user_id", "task_count", "date_start", "date", "stage_id"]
    out(o.search_read("project.project", domain, fields, a.limit, "name"))


def cmd_tasks(o, a):
    domain = []
    if a.project:
        domain.append(["project_id", "=", int(a.project)] if a.project.isdigit() else ["project_id", "ilike", a.project])
    if a.query:
        domain.append(["name", "ilike", a.query])
    if a.mine:
        domain.append(["user_ids", "in", [o.uid]])
    if not a.all:
        domain.append(["state", "not in", ["1_done", "1_canceled"]])
    fields = ["name", "project_id", "stage_id", "state", "user_ids", "partner_id", "date_deadline", "priority",
              "allocated_hours", "effective_hours", "remaining_hours"]
    out(o.search_read("project.task", domain, fields, a.limit, "priority desc, date_deadline, id"))


def cmd_timesheet_list(o, a):
    since = (dt.date.today() - dt.timedelta(days=a.days)).isoformat()
    domain = [["date", ">=", since], ["project_id", "!=", False]]
    if a.mine:
        domain.append(["user_id", "=", o.uid])
    fields = ["date", "project_id", "task_id", "name", "unit_amount", "employee_id"]
    out(o.search_read("account.analytic.line", domain, fields, a.limit, "date desc"))


def cmd_timesheet_add(o, a):
    task = o.call("project.task", "read", [[a.task_id]], {"fields": ["project_id", "name"]})
    if not task:
        fail(f"Aufgabe {a.task_id} nicht gefunden.")
    values = {"task_id": a.task_id, "project_id": task[0]["project_id"][0], "name": a.description,
              "unit_amount": a.hours, "date": a.date or _today()}
    new_id = o.call("account.analytic.line", "create", [values])
    out({"id": new_id, "task": task[0]["name"], "project": task[0]["project_id"][1], "hours": a.hours,
         "date": values["date"]})


# --- CLI -------------------------------------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(prog="odoo.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version", help="Server-Version und Verbindung").set_defaults(fn=cmd_version)
    sub.add_parser("me", help="angemeldeter Benutzer").set_defaults(fn=cmd_me)

    s = sub.add_parser("models", help="Modelle auflisten"); s.set_defaults(fn=cmd_models)
    s.add_argument("filter", nargs="?", help="Teilstring des technischen Namens, z. B. sale")
    s.add_argument("--all", action="store_true", help="auch transiente Modelle (Wizards)")
    s.add_argument("--limit", type=int, default=200)

    s = sub.add_parser("fields", help="Felder eines Modells"); s.set_defaults(fn=cmd_fields)
    s.add_argument("model"); s.add_argument("--filter", help="Teilstring in Feldname oder Beschriftung")
    s.add_argument("--help-text", dest="help", action="store_true", help="Hilfetexte mit ausgeben")

    s = sub.add_parser("search", help="search_read auf ein Modell"); s.set_defaults(fn=cmd_search)
    s.add_argument("model"); s.add_argument("--domain", help='JSON-Domain, z. B. \'[["state","=","draft"]]\'')
    s.add_argument("--fields", help="kommagetrennt (Default: alle)"); s.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    s.add_argument("--order"); s.add_argument("--offset", type=int, default=0)

    s = sub.add_parser("count", help="Anzahl Datensaetze"); s.set_defaults(fn=cmd_count)
    s.add_argument("model"); s.add_argument("--domain")

    s = sub.add_parser("read", help="Datensaetze per ID lesen"); s.set_defaults(fn=cmd_read)
    s.add_argument("model"); s.add_argument("ids", nargs="+"); s.add_argument("--fields")

    s = sub.add_parser("create", help="Datensatz anlegen"); s.set_defaults(fn=cmd_create)
    s.add_argument("model"); s.add_argument("--values", required=True, help="JSON-Objekt mit Feldwerten")

    s = sub.add_parser("write", help="Datensaetze aendern"); s.set_defaults(fn=cmd_write)
    s.add_argument("model"); s.add_argument("ids", nargs="+"); s.add_argument("--values", required=True)

    s = sub.add_parser("delete", help="Datensaetze loeschen (--yes)"); s.set_defaults(fn=cmd_delete)
    s.add_argument("model"); s.add_argument("ids", nargs="+"); s.add_argument("--yes", action="store_true")

    s = sub.add_parser("call", help="beliebige Modellmethode (zustandsaendernd nur mit --yes)"); s.set_defaults(fn=cmd_call)
    s.add_argument("model"); s.add_argument("method"); s.add_argument("--ids", nargs="*", help="Record-IDs als erstes Argument")
    s.add_argument("--args", help="JSON-Liste weiterer Positionsargumente"); s.add_argument("--kwargs", help="JSON-Objekt")
    s.add_argument("--yes", action="store_true")

    s = sub.add_parser("contacts", help="Kontakte suchen (Name, E-Mail, Telefon)"); s.set_defaults(fn=cmd_contacts)
    s.add_argument("query"); s.add_argument("--companies", action="store_true", help="nur Firmen"); s.add_argument("--limit", type=int, default=20)

    s = sub.add_parser("leads", help="CRM: Leads und Opportunities"); s.set_defaults(fn=cmd_leads)
    s.add_argument("query", nargs="?"); s.add_argument("--stage"); s.add_argument("--all", action="store_true", help="auch archivierte")
    s.add_argument("--limit", type=int, default=30)

    s = sub.add_parser("sales", help="Verkauf: Angebote und Auftraege"); s.set_defaults(fn=cmd_sales)
    s.add_argument("--state", choices=["draft", "sent", "sale", "cancel"]); s.add_argument("--partner"); s.add_argument("--limit", type=int, default=30)

    s = sub.add_parser("invoices", help="Rechnungen (Kunden/Lieferanten)"); s.set_defaults(fn=cmd_invoices)
    s.add_argument("--type", choices=["out", "in", "out_refund", "in_refund"], default="out", help="out = Kundenrechnung, in = Lieferantenrechnung")
    s.add_argument("--state", choices=["draft", "posted", "cancel"]); s.add_argument("--unpaid", action="store_true")
    s.add_argument("--partner"); s.add_argument("--limit", type=int, default=30)

    s = sub.add_parser("purchases", help="Einkauf: Bestellungen"); s.set_defaults(fn=cmd_purchases)
    s.add_argument("--state", choices=["draft", "sent", "to approve", "purchase", "done", "cancel"]); s.add_argument("--partner")
    s.add_argument("--limit", type=int, default=30)

    s = sub.add_parser("projects", help="Projekte"); s.set_defaults(fn=cmd_projects)
    s.add_argument("--all", action="store_true"); s.add_argument("--limit", type=int, default=50)

    s = sub.add_parser("tasks", help="Aufgaben"); s.set_defaults(fn=cmd_tasks)
    s.add_argument("query", nargs="?"); s.add_argument("--project", help="ID oder Namensteil"); s.add_argument("--mine", action="store_true")
    s.add_argument("--all", action="store_true", help="auch erledigte/abgebrochene"); s.add_argument("--limit", type=int, default=50)

    ts = sub.add_parser("timesheet", help="Zeiterfassung"); tsub = ts.add_subparsers(dest="tcmd", required=True)
    s = tsub.add_parser("list", help="Buchungen der letzten Tage"); s.set_defaults(fn=cmd_timesheet_list)
    s.add_argument("--days", type=int, default=7); s.add_argument("--mine", action="store_true"); s.add_argument("--limit", type=int, default=100)
    s = tsub.add_parser("add", help="Zeit auf eine Aufgabe buchen"); s.set_defaults(fn=cmd_timesheet_add)
    s.add_argument("task_id", type=int); s.add_argument("hours", type=float); s.add_argument("description")
    s.add_argument("--date", help="YYYY-MM-DD (Default heute)")

    a = p.parse_args()
    a.fn(Odoo(), a)


if __name__ == "__main__":
    main()
