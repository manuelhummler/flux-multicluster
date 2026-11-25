"""Tests fuer den Realm-Setup-Job (Doku 4j). Ausfuehren:
    python -m unittest discover -s infrastructure/karibik/keycloak-beg/cleanup -v
"""
import unittest

import realm_setup


class ScopeTest(unittest.TestCase):
    def test_scope_missing_when_not_listed(self):
        self.assertIsNone(realm_setup.find_scope([{"id": "1", "name": "profile"}], "beg-roles"))

    def test_scope_found_by_name(self):
        self.assertEqual(realm_setup.find_scope([{"id": "7", "name": "beg-roles"}], "beg-roles"), "7")

    def test_scope_payload_maps_realm_roles_to_top_level_roles_claim(self):
        payload = realm_setup.scope_payload()
        self.assertEqual(payload["name"], "beg-roles")
        self.assertEqual(payload["protocol"], "openid-connect")
        mapper = payload["protocolMappers"][0]
        self.assertEqual(mapper["protocolMapper"], "oidc-usermodel-realm-role-mapper")
        self.assertEqual(mapper["config"]["claim.name"], "roles")
        self.assertEqual(mapper["config"]["multivalued"], "true")
        self.assertEqual(mapper["config"]["access.token.claim"], "true")
        self.assertEqual(mapper["config"]["id.token.claim"], "true")


class AdminEventsTest(unittest.TestCase):
    """Admin-Events halten fest, welcher Client einen Nutzer angelegt hat – Grundlage des Aufraeum-Jobs."""

    def test_admin_events_enabled_when_off(self):
        changed, cfg = realm_setup.events_config_with_admin_events({"adminEventsEnabled": False, "eventsEnabled": False})
        self.assertTrue(changed)
        self.assertTrue(cfg["adminEventsEnabled"])
        self.assertFalse(cfg["eventsEnabled"])

    def test_unchanged_when_already_enabled(self):
        changed, _ = realm_setup.events_config_with_admin_events({"adminEventsEnabled": True})
        self.assertFalse(changed)

    def test_missing_flag_counts_as_off(self):
        changed, cfg = realm_setup.events_config_with_admin_events({})
        self.assertTrue(changed)
        self.assertTrue(cfg["adminEventsEnabled"])


class ServiceAccountRolesTest(unittest.TestCase):
    """Der Aufraeum-Client braucht view-events, um die Admin-Events zu lesen."""

    def test_missing_role_is_returned(self):
        available = [{"id": "r1", "name": "view-events"}, {"id": "r2", "name": "manage-users"}]
        current = [{"id": "r2", "name": "manage-users"}]
        self.assertEqual(realm_setup.missing_roles(available, current, ["view-events"]),
                         [{"id": "r1", "name": "view-events"}])

    def test_nothing_missing_when_already_mapped(self):
        available = [{"id": "r1", "name": "view-events"}]
        self.assertEqual(realm_setup.missing_roles(available, available, ["view-events"]), [])


class PreviewUserAdminRoleTest(unittest.TestCase):
    """KeycloakOIDCClient.serviceAccountRoles nimmt nur Realm-Rollen (Spike A 2026-11-25). Die Nutzerverwaltung der
    Preview-Clients laeuft deshalb ueber eine zusammengesetzte Realm-Rolle mit den realm-management-Rechten."""

    def test_role_payload_is_composite(self):
        payload = realm_setup.preview_user_admin_role_payload()
        self.assertEqual(payload["name"], "beg-preview-user-admin")
        self.assertTrue(payload["composite"])

    def test_composites_cover_what_keycloakservice_needs(self):
        self.assertEqual(set(realm_setup.PREVIEW_USER_ADMIN_COMPOSITES),
                         {"manage-users", "view-users", "query-users", "query-groups"})


class ScopeMappingsTest(unittest.TestCase):
    """Der Operator legt Clients mit fullScopeAllowed=false an (Spike A 2026-11-25): Tokens enthalten nur Rollen aus
    den Scope-Mappings. Die Mappings haengen deshalb am Default-Scope beg-roles, den alle Preview-Clients erben."""

    def test_realm_roles_cover_jmix_codes_portal_and_user_admin(self):
        self.assertTrue({"admin", "system-full-access", "system-user-access", "system-user-readonly",
                         "bpm-process-task-performer", "portal-user", "beg-preview-user-admin"}
                        <= set(realm_setup.SCOPE_REALM_ROLES))

    def test_realm_management_roles_match_user_admin_composites(self):
        self.assertEqual(set(realm_setup.SCOPE_REALM_MANAGEMENT_ROLES),
                         set(realm_setup.PREVIEW_USER_ADMIN_COMPOSITES))


class AdminApiScopeTest(unittest.TestCase):
    """Keycloak 26.x verlangt fuer die Admin-API die Audience aus Scope kc-admin-api (Spike A 2026-11-25): ohne sie
    401 trotz realm-management-Rollen. Service-Account-Clients brauchen ihn als Default-Scope."""

    def test_only_service_account_clients_are_selected(self):
        clients = [{"id": "1", "clientId": "beg-e2e-admin", "serviceAccountsEnabled": True},
                   {"id": "2", "clientId": "spike-portal", "serviceAccountsEnabled": False},
                   {"id": "3", "clientId": "beg-mr-7-crm", "serviceAccountsEnabled": True},
                   {"id": "4", "clientId": "realm-management", "serviceAccountsEnabled": True}]
        self.assertEqual(realm_setup.service_account_client_ids(clients), ["1", "3"])

    def test_admin_api_scope_name(self):
        self.assertEqual(realm_setup.ADMIN_API_SCOPE, "kc-admin-api")


if __name__ == "__main__":
    unittest.main()
