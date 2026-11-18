#!/usr/bin/env python3
"""m365-graph: Microsoft Graph fuer Hermes.

Dateien (OneDrive, SharePoint) nur lesend; Mail lesen und Entwuerfe anlegen (kein Senden: die App hat
kein Mail.Send); Kalender lesen und schreiben. Login einmalig per Device-Code (Befehl `auth`), Token-Cache
unter HERMES_HOME/m365_graph_token.json (0600). Ausgabe immer JSON.

Quelle: infrastructure/karibik/hermes/m365-graph/graph.py im Flux-Repo (ConfigMap m365-graph-skill), der
Init-Container install-extras kopiert sie nach skills/productivity/m365-graph/scripts/. Doku 4e "Microsoft Graph".
"""
import argparse
import json
import os
import re
import stat
import sys
import urllib.parse

import msal
import requests

HERMES_HOME = os.environ.get("HERMES_HOME", "/opt/data")
TOKEN_FILE = os.path.join(HERMES_HOME, "m365_graph_token.json")
CACHE_DIR = os.path.join(HERMES_HOME, "cache", "m365-graph")
TENANT_ID = "7804793a-7aea-4b72-af44-80d502e33d8d"
CLIENT_ID = "37e2fc1b-cfbf-4c14-8e17-b9e31bde2ee6"  # Entra-App hermes-graph (Public Client, kein Secret)
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["Files.Read.All", "Sites.Read.All", "Mail.ReadWrite", "Calendars.ReadWrite", "User.Read"]
GRAPH = "https://graph.microsoft.com/v1.0"
TIMEZONE = "Europe/Berlin"
# Laufwerke: Kurzname -> Graph-Drive-Pfad. office/familie = die gespiegelten SharePoint-Bibliotheken.
DRIVES = {
    "onedrive": "/me/drive",
    "office": "/drives/b!ljQgUs4UzEayVUMMwKRdg_V6WjwsemdMlxnFuwIARVELmSD0mEpcRY_6YkElqKGR",
    "familie": "/drives/b!g5C__xH8-ECVks8A44n0Hwrnp8vgXfdNsTBPph0Oyv4H5KhAZlu9RrYAUK5O3L59",
}


def out(data, code=0):
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(code)


def fail(msg, **extra):
    out({"error": msg, **extra}, 1)


# --- Auth -------------------------------------------------------------------------------------------------

def _cache():
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            cache.deserialize(f.read())
    return cache


def _save(cache):
    if cache.has_state_changed:
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(cache.serialize())


def _app(cache):
    return msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)


def cmd_auth(args):
    cache = _cache()
    app = _app(cache)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        fail("Device-Code-Flow konnte nicht gestartet werden", details=flow)
    print(json.dumps({"status": "waiting", "url": flow["verification_uri"], "code": flow["user_code"],
                      "expires_in": flow.get("expires_in"),
                      "hint": "Im Browser anmelden; dieser Befehl wartet bis zur Bestaetigung."},
                     ensure_ascii=False), flush=True)
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        fail("Login fehlgeschlagen", details={k: result.get(k) for k in ("error", "error_description")})
    _save(cache)
    out({"status": "ok", "account": result.get("id_token_claims", {}).get("preferred_username"),
         "token_file": TOKEN_FILE})


def token():
    cache = _cache()
    app = _app(cache)
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    _save(cache)
    if not result or "access_token" not in result:
        fail("Kein gueltiges Token. Einmalig `graph.py auth` ausfuehren (Device-Code-Login).")
    return result["access_token"]


# --- Graph-Helfer ----------------------------------------------------------------------------------------

def req(method, path, params=None, body=None, headers=None, raw=False):
    h = {"Authorization": f"Bearer {token()}", "Accept": "application/json"}
    if headers:
        h.update(headers)
    url = path if path.startswith("http") else GRAPH + path
    r = requests.request(method, url, params=params, json=body, headers=h, timeout=60)
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {"message": r.text[:300]}
        fail(f"Graph {r.status_code}: {err.get('message', '')}", code=err.get("code"), path=path)
    if raw:
        return r
    return r.json() if r.content else {}


def paged(path, params, limit):
    items, url, p = [], path, params
    while url and len(items) < limit:
        data = req("GET", url, p)
        items.extend(data.get("value", []))
        url, p = data.get("@odata.nextLink"), None
    return items[:limit]


def pick(d, *keys):
    return {k: d.get(k) for k in keys if k in d}


# --- Dateien (nur lesend) --------------------------------------------------------------------------------

def drive_root(name):
    if name in DRIVES:
        return DRIVES[name]
    if name.startswith("b!"):
        return f"/drives/{name}"
    fail(f"Unbekanntes Laufwerk '{name}'", known=list(DRIVES))


def item_path(root, path):
    path = (path or "").strip("/")
    return f"{root}/root" if not path else f"{root}/root:/{urllib.parse.quote(path)}:"


def fmt_item(i):
    d = pick(i, "name", "size", "lastModifiedDateTime", "webUrl", "id")
    d["type"] = "folder" if "folder" in i else "file"
    if "folder" in i:
        d["childCount"] = i["folder"].get("childCount")
    pr = i.get("parentReference") or {}
    if pr.get("path"):
        d["path"] = re.sub(r"^/drives?/[^/]+/root:?", "", pr["path"]) + "/" + i.get("name", "")
    return d


def cmd_drive_ls(args):
    root = drive_root(args.drive)
    items = paged(f"{item_path(root, args.path)}/children", {"$top": 200}, args.top)
    out([fmt_item(i) for i in items])


def cmd_drive_search(args):
    root = drive_root(args.drive)
    q = urllib.parse.quote(args.query.replace("'", "''"))
    items = paged(f"{root}/root/search(q='{q}')", {"$top": 50}, args.top)
    out([fmt_item(i) for i in items])


def cmd_drive_info(args):
    root = drive_root(args.drive)
    i = req("GET", item_path(root, args.path))
    out(fmt_item(i) | pick(i, "createdDateTime", "createdBy", "lastModifiedBy"))


def cmd_drive_get(args):
    root = drive_root(args.drive)
    i = req("GET", item_path(root, args.path))
    if "file" not in i:
        fail("Kein File (Ordner?)", name=i.get("name"))
    url = i.get("@microsoft.graph.downloadUrl")
    os.makedirs(CACHE_DIR, exist_ok=True)
    dest = os.path.join(args.out or CACHE_DIR, i["name"])
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    out({"saved": dest, "size": i.get("size"), "hint": "Lesekopie; Aenderungen gehen NICHT zurueck."})


def cmd_drive_versions(args):
    root = drive_root(args.drive)
    i = req("GET", item_path(root, args.path), {"$select": "id,name"})
    v = req("GET", f"{root}/items/{i['id']}/versions")
    out([pick(x, "id", "lastModifiedDateTime", "size") | {"by": (x.get("lastModifiedBy") or {}).get("user", {}).get("displayName")}
         for x in v.get("value", [])])


# --- Mail (lesen, suchen, Entwuerfe) ------------------------------------------------------------------------

MAIL_SELECT = "id,subject,from,toRecipients,receivedDateTime,isRead,hasAttachments,bodyPreview,webLink,conversationId"


def fmt_mail(m):
    d = pick(m, "id", "subject", "receivedDateTime", "isRead", "hasAttachments", "bodyPreview", "webLink")
    d["from"] = (m.get("from") or {}).get("emailAddress", {}).get("address")
    d["to"] = [r.get("emailAddress", {}).get("address") for r in m.get("toRecipients", [])]
    return d


def cmd_mail_list(args):
    params = {"$top": 50, "$select": MAIL_SELECT, "$orderby": "receivedDateTime desc"}
    if args.unread:
        params["$filter"] = "isRead eq false"
    out([fmt_mail(m) for m in paged(f"/me/mailFolders/{args.folder}/messages", params, args.top)])


def cmd_mail_search(args):
    params = {"$top": 50, "$select": MAIL_SELECT, "$search": f'"{args.query}"'}
    out([fmt_mail(m) for m in paged("/me/messages", params, args.top)])


def cmd_mail_read(args):
    m = req("GET", f"/me/messages/{args.id}", {"$select": MAIL_SELECT + ",body,ccRecipients"},
            headers={"Prefer": 'outlook.body-content-type="text"'})
    d = fmt_mail(m)
    d["cc"] = [r.get("emailAddress", {}).get("address") for r in m.get("ccRecipients", [])]
    d["body"] = (m.get("body") or {}).get("content")
    if args.attachments:
        a = req("GET", f"/me/messages/{args.id}/attachments", {"$select": "id,name,size,contentType"})
        d["attachments"] = a.get("value", [])
    out(d)


def _recipients(addrs):
    return [{"emailAddress": {"address": a.strip()}} for a in (addrs or []) if a.strip()]


def cmd_mail_draft(args):
    body = {"body": {"contentType": "Text", "content": args.body}}
    if args.reply_to:
        d = req("POST", f"/me/messages/{args.reply_to}/{'createReplyAll' if args.reply_all else 'createReply'}",
                body={"comment": args.body})
        if args.to or args.cc:
            req("PATCH", f"/me/messages/{d['id']}", body={**({"toRecipients": _recipients(args.to)} if args.to else {}),
                                                         **({"ccRecipients": _recipients(args.cc)} if args.cc else {})})
    else:
        if not args.to or not args.subject:
            fail("--to und --subject sind Pflicht (ausser bei --reply-to)")
        body.update({"subject": args.subject, "toRecipients": _recipients(args.to), "ccRecipients": _recipients(args.cc)})
        d = req("POST", "/me/messages", body=body)
    out({"status": "draft", "id": d.get("id"), "subject": d.get("subject"), "webLink": d.get("webLink"),
         "hint": "Liegt im Ordner Entwuerfe. Senden kann nur der Nutzer in Outlook; die App hat kein Mail.Send."})


# --- Kalender (lesen und schreiben) -----------------------------------------------------------------------

CAL_HEADERS = {"Prefer": f'outlook.timezone="{TIMEZONE}"'}


def fmt_event(e):
    d = pick(e, "id", "subject", "isAllDay", "webLink", "isCancelled")
    d["start"] = (e.get("start") or {}).get("dateTime")
    d["end"] = (e.get("end") or {}).get("dateTime")
    d["location"] = (e.get("location") or {}).get("displayName")
    d["organizer"] = ((e.get("organizer") or {}).get("emailAddress") or {}).get("address")
    d["attendees"] = [{"address": a.get("emailAddress", {}).get("address"),
                       "response": (a.get("status") or {}).get("response")} for a in e.get("attendees", [])]
    if "bodyPreview" in e:
        d["bodyPreview"] = e["bodyPreview"]
    return d


def cmd_cal_list(args):
    params = {"startDateTime": args.start, "endDateTime": args.end, "$orderby": "start/dateTime", "$top": 100,
              "$select": "id,subject,start,end,location,organizer,attendees,isAllDay,webLink,isCancelled,bodyPreview"}
    items = []
    url, p = "/me/calendarView", params
    while url and len(items) < args.top:
        data = req("GET", url, p, headers=CAL_HEADERS)
        items.extend(data.get("value", []))
        url, p = data.get("@odata.nextLink"), None
    out([fmt_event(e) for e in items[:args.top]])


def cmd_cal_get(args):
    e = req("GET", f"/me/events/{args.id}", headers={**CAL_HEADERS, "Prefer": CAL_HEADERS["Prefer"] + ', outlook.body-content-type="text"'})
    d = fmt_event(e)
    d["body"] = (e.get("body") or {}).get("content")
    out(d)


def _event_body(args):
    b = {}
    if args.subject:
        b["subject"] = args.subject
    if args.start:
        b["start"] = {"dateTime": args.start, "timeZone": TIMEZONE}
    if args.end:
        b["end"] = {"dateTime": args.end, "timeZone": TIMEZONE}
    if args.location:
        b["location"] = {"displayName": args.location}
    if args.body:
        b["body"] = {"contentType": "Text", "content": args.body}
    if args.attendees:
        b["attendees"] = [{"emailAddress": {"address": a.strip()}, "type": "required"} for a in args.attendees if a.strip()]
    if getattr(args, "all_day", False):
        b["isAllDay"] = True
    return b


def cmd_cal_create(args):
    if not (args.subject and args.start and args.end):
        fail("--subject, --start und --end sind Pflicht (ISO 8601, z. B. 2026-11-20T14:00:00)")
    e = req("POST", "/me/events", body=_event_body(args), headers=CAL_HEADERS)
    out({"status": "created"} | fmt_event(e))


def cmd_cal_update(args):
    b = _event_body(args)
    if not b:
        fail("Nichts zu aendern angegeben")
    e = req("PATCH", f"/me/events/{args.id}", body=b, headers=CAL_HEADERS)
    out({"status": "updated"} | fmt_event(e))


def cmd_cal_delete(args):
    if not args.yes:
        fail("Loeschen nur mit --yes, nachdem der Nutzer den Termin ausdruecklich benannt hat")
    req("DELETE", f"/me/events/{args.id}")
    out({"status": "deleted", "id": args.id})


def cmd_me(args):
    out(pick(req("GET", "/me", {"$select": "displayName,mail,userPrincipalName"}), "displayName", "mail", "userPrincipalName"))


# --- CLI -----------------------------------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="einmaliger Device-Code-Login").set_defaults(fn=cmd_auth)
    sub.add_parser("me", help="angemeldetes Konto").set_defaults(fn=cmd_me)

    d = sub.add_parser("drive", help="Dateien (nur lesend)").add_subparsers(dest="sub", required=True)
    for name, fn, with_path in (("ls", cmd_drive_ls, True), ("search", cmd_drive_search, False),
                                ("info", cmd_drive_info, True), ("get", cmd_drive_get, True), ("versions", cmd_drive_versions, True)):
        s = d.add_parser(name)
        s.add_argument("--drive", default="onedrive", help="onedrive | office | familie | b!<drive_id>")
        if with_path:
            s.add_argument("path", nargs="?" if name == "ls" else None, default="", help="Pfad relativ zur Laufwerkswurzel")
        else:
            s.add_argument("query")
        s.add_argument("--top", type=int, default=50)
        if name == "get":
            s.add_argument("--out", help="Zielordner (Default: HERMES_HOME/cache/m365-graph)")
        s.set_defaults(fn=fn)

    m = sub.add_parser("mail", help="Mail lesen, suchen, Entwuerfe").add_subparsers(dest="sub", required=True)
    s = m.add_parser("list"); s.add_argument("--folder", default="inbox"); s.add_argument("--unread", action="store_true")
    s.add_argument("--top", type=int, default=20); s.set_defaults(fn=cmd_mail_list)
    s = m.add_parser("search"); s.add_argument("query"); s.add_argument("--top", type=int, default=20); s.set_defaults(fn=cmd_mail_search)
    s = m.add_parser("read"); s.add_argument("id"); s.add_argument("--attachments", action="store_true"); s.set_defaults(fn=cmd_mail_read)
    s = m.add_parser("draft", help="Entwurf anlegen (nie senden)")
    s.add_argument("--to", nargs="*"); s.add_argument("--cc", nargs="*"); s.add_argument("--subject")
    s.add_argument("--body", required=True); s.add_argument("--reply-to", help="Message-ID: Antwortentwurf")
    s.add_argument("--reply-all", action="store_true"); s.set_defaults(fn=cmd_mail_draft)

    c = sub.add_parser("cal", help="Kalender lesen und schreiben").add_subparsers(dest="sub", required=True)
    s = c.add_parser("list"); s.add_argument("--start", required=True, help="ISO 8601, z. B. 2026-11-18T00:00:00")
    s.add_argument("--end", required=True); s.add_argument("--top", type=int, default=100); s.set_defaults(fn=cmd_cal_list)
    s = c.add_parser("get"); s.add_argument("id"); s.set_defaults(fn=cmd_cal_get)
    for name, fn in (("create", cmd_cal_create), ("update", cmd_cal_update)):
        s = c.add_parser(name)
        if name == "update":
            s.add_argument("id")
        s.add_argument("--subject"); s.add_argument("--start"); s.add_argument("--end"); s.add_argument("--location")
        s.add_argument("--body"); s.add_argument("--attendees", nargs="*"); s.add_argument("--all-day", action="store_true")
        s.set_defaults(fn=fn)
    s = c.add_parser("delete"); s.add_argument("id"); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_cal_delete)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
