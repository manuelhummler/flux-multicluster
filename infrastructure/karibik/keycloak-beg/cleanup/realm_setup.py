"""Realm-Setup fuer Realm "beg" (Doku 4j), idempotent, nur Python-Stdlib. Laeuft als Job nach dem Realm-Import.

Warum ein Job statt Realm-Import: KeycloakRealmImport wirkt nur einmal, und ein Import mit eigener
clientScopes-Liste unterdrueckt die eingebauten Scopes (profile, email, roles, ...). Deshalb bleibt der Import beim
Standard, und dieser Job ergaenzt, was die Previews brauchen – beliebig oft wiederholbar:
1. Client-Scope "beg-roles" (Realm-Rollen als Top-Level-Claim "roles" fuer Jmix) als Realm-Default-Scope:
   KeycloakOIDCClient kennt keine Protocol-Mapper, neue Preview-Clients erben ihn so.
2. Admin-Events an: sie halten fest, welcher Client (beg-<id>-crm) einen Nutzer angelegt hat. Der Aufraeum-Job
   loescht damit die Nutzer verschwundener Previews, ohne dass die App Preview-Code braucht.
3. Service-Account von beg-preview-cleanup bekommt realm-management/view-events (Admin-Events lesen).

Umgebung: KEYCLOAK_URL, REALM, ADMIN_CLIENT_ID, ADMIN_CLIENT_SECRET (Service-Account im master-Realm, Secret beg-admin).
"""
import os
import sys
import urllib.error

from cleanup import _request

SCOPE_NAME = "beg-roles"


def find_scope(scopes, name):
    for scope in scopes:
        if scope.get("name") == name:
            return scope["id"]
    return None


def scope_payload():
    return {
        "name": SCOPE_NAME,
        "protocol": "openid-connect",
        "description": "Realm-Rollen als Claim roles (jmix.oidc.default-claims-roles-mapper.roles-claim-name)",
        "attributes": {"include.in.token.scope": "false", "display.on.consent.screen": "false"},
        "protocolMappers": [{
            "name": "realm-roles-to-roles-claim",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-realm-role-mapper",
            "consentRequired": False,
            "config": {"multivalued": "true", "userinfo.token.claim": "true", "id.token.claim": "true",
                       "access.token.claim": "true", "claim.name": "roles", "jsonType.label": "String"},
        }],
    }


def events_config_with_admin_events(cfg):
    """(changed, cfg) – schaltet Admin-Events ein, laesst alle anderen Event-Einstellungen unveraendert."""
    if cfg.get("adminEventsEnabled"):
        return False, cfg
    new = dict(cfg)
    new["adminEventsEnabled"] = True
    return True, new


CLEANUP_CLIENT = "beg-preview-cleanup"
CLEANUP_EXTRA_ROLES = ["view-events"]


def missing_roles(available, current, wanted):
    """Rollen aus `wanted`, die in `current` fehlen – als Repraesentationen aus `available`."""
    have = {r["name"] for r in current}
    return [r for r in available if r["name"] in wanted and r["name"] not in have]


def _put_json(url, token, body):
    import json
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PUT", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def _post_json(url, token, body):
    import json
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def main():
    base = os.environ.get("KEYCLOAK_URL", "http://beg-service.keycloak.svc.cluster.local:8080")
    realm = os.environ.get("REALM", "beg")
    token = _request(f"{base}/realms/master/protocol/openid-connect/token", method="POST", data={
        "grant_type": "client_credentials",
        "client_id": os.environ["ADMIN_CLIENT_ID"],
        "client_secret": os.environ["ADMIN_CLIENT_SECRET"],
    })["access_token"]
    admin = f"{base}/admin/realms/{realm}"
    try:
        scopes = _request(f"{admin}/client-scopes", token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"Realm {realm} existiert (noch) nicht – Realm-Import abwarten, Job wird wiederholt")
            return 1
        raise

    scope_id = find_scope(scopes, SCOPE_NAME)
    if scope_id is None:
        _post_json(f"{admin}/client-scopes", token, scope_payload())
        scope_id = find_scope(_request(f"{admin}/client-scopes", token), SCOPE_NAME)
        print(f"Client-Scope {SCOPE_NAME} angelegt")
    else:
        print(f"Client-Scope {SCOPE_NAME} vorhanden")
    defaults = _request(f"{admin}/default-default-client-scopes", token)
    if find_scope(defaults, SCOPE_NAME) is None:
        _put_json(f"{admin}/default-default-client-scopes/{scope_id}", token, {})
        print(f"{SCOPE_NAME} als Realm-Default-Scope gesetzt")

    cleanup_id = _request(f"{admin}/clients?clientId={CLEANUP_CLIENT}", token)[0]["id"]
    sa_user = _request(f"{admin}/clients/{cleanup_id}/service-account-user", token)["id"]
    rm_id = _request(f"{admin}/clients?clientId=realm-management", token)[0]["id"]
    missing = missing_roles(_request(f"{admin}/clients/{rm_id}/roles", token),
                            _request(f"{admin}/users/{sa_user}/role-mappings/clients/{rm_id}", token),
                            CLEANUP_EXTRA_ROLES)
    if missing:
        _post_json(f"{admin}/users/{sa_user}/role-mappings/clients/{rm_id}", token, missing)
        print(f"{CLEANUP_CLIENT}: Rollen ergaenzt {[r['name'] for r in missing]}")

    changed, cfg = events_config_with_admin_events(_request(f"{admin}/events/config", token))
    if changed:
        _put_json(f"{admin}/events/config", token, cfg)
        print("Admin-Events eingeschaltet")
    else:
        print("Admin-Events bereits an")
    return 0


if __name__ == "__main__":
    sys.exit(main())
