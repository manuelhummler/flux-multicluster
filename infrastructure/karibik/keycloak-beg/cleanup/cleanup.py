"""Aufraeum-Job der Preview-Umgebungen von beg-verwaltung (Doku 4j), nur Python-Stdlib.

Loescht im Realm "beg" der Keycloak-Instanz "beg":
- Nutzer mit Attribut preview=<id>, deren Preview-Namespace beg-<id> nicht mehr existiert
  (die App setzt das Attribut, KeyCloakService.buildCreateUserPayload),
- E2E-Nutzer (username "e2e-...") aelter als 24 h (Teardown der Suite ist fehlgeschlagen),
- Clients beg-<id>-crm / beg-<id>-portal ohne Namespace (Keycloak war beim Abbau der Preview nicht erreichbar).
Feste Testnutzer (ohne preview-Attribut) und Service-Account-Nutzer bleiben immer.

Umgebung: KEYCLOAK_URL, REALM, CLIENT_ID, CLIENT_SECRET, DRY_RUN=1 (nur ausgeben).
Kubernetes-Zugriff ueber den ServiceAccount des Pods (nur "list namespaces").
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

E2E_MAX_AGE_MS = 24 * 3600 * 1000
CLIENT_PATTERN = re.compile(r"^beg-(.+)-(crm|portal)$")
NAMESPACE_PATTERN = re.compile(r"^beg-(mr-\d+|main)$")


def preview_ids_from_namespaces(names):
    """Preview-IDs (mr-<iid>, main) aus Namespace-Namen beg-<id>."""
    return {m.group(1) for m in (NAMESPACE_PATTERN.match(n) for n in names) if m}


def select_users_to_delete(users, existing_ids, now_ms):
    result = []
    for user in users:
        if user.get("serviceAccountClientId") or user.get("username", "").startswith("service-account-"):
            continue
        preview = (user.get("attributes") or {}).get("preview") or []
        if preview and preview[0] not in existing_ids:
            result.append(user["id"])
        elif user.get("username", "").startswith("e2e-") and \
                user.get("createdTimestamp", now_ms) < now_ms - E2E_MAX_AGE_MS:
            result.append(user["id"])
    return result


def select_clients_to_delete(clients, existing_ids):
    result = []
    for client in clients:
        m = CLIENT_PATTERN.match(client.get("clientId", ""))
        if m and m.group(1) not in existing_ids:
            result.append(client["id"])
    return result


def _request(url, token=None, method="GET", data=None, context=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30, context=context) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def _paged(url, token, page=200):
    first = 0
    while True:
        sep = "&" if "?" in url else "?"
        batch = _request(f"{url}{sep}first={first}&max={page}", token)
        yield from batch
        if len(batch) < page:
            return
        first += page


def _namespaces():
    sa = "/var/run/secrets/kubernetes.io/serviceaccount"
    token = open(f"{sa}/token").read().strip()
    ctx = ssl.create_default_context(cafile=f"{sa}/ca.crt")
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    data = _request(f"https://{host}:{port}/api/v1/namespaces", token, context=ctx)
    return [item["metadata"]["name"] for item in data["items"]]


def main():
    base = os.environ.get("KEYCLOAK_URL", "https://auth-beg.hummler.com")
    realm = os.environ.get("REALM", "beg")
    dry_run = os.environ.get("DRY_RUN") == "1"
    token = _request(f"{base}/realms/{realm}/protocol/openid-connect/token", method="POST", data={
        "grant_type": "client_credentials",
        "client_id": os.environ.get("CLIENT_ID", "beg-e2e-admin"),
        "client_secret": os.environ["CLIENT_SECRET"],
    })["access_token"]

    existing = preview_ids_from_namespaces(_namespaces())
    admin = f"{base}/admin/realms/{realm}"
    users = list(_paged(f"{admin}/users?briefRepresentation=false", token))
    clients = list(_paged(f"{admin}/clients", token))
    user_ids = select_users_to_delete(users, existing, int(time.time() * 1000))
    client_ids = select_clients_to_delete(clients, existing)

    names = {u["id"]: u.get("username") for u in users}
    client_names = {c["id"]: c.get("clientId") for c in clients}
    print(f"Previews vorhanden: {sorted(existing)}; Nutzer gesamt {len(users)}, Clients gesamt {len(clients)}")
    for uid in user_ids:
        print(("[dry-run] " if dry_run else "") + f"loesche Nutzer {names[uid]}")
        if not dry_run:
            _request(f"{admin}/users/{uid}", token, method="DELETE")
    for cid in client_ids:
        print(("[dry-run] " if dry_run else "") + f"loesche Client {client_names[cid]}")
        if not dry_run:
            _request(f"{admin}/clients/{cid}", token, method="DELETE")
    print(f"fertig: {len(user_ids)} Nutzer, {len(client_ids)} Clients")
    return 0


if __name__ == "__main__":
    sys.exit(main())
