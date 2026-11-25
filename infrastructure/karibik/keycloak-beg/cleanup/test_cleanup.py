"""Tests fuer die Auswahllogik des Preview-Aufraeum-Jobs (Doku 4j). Ausfuehren:
    python -m unittest discover -s infrastructure/karibik/keycloak-beg/cleanup -v
"""
import unittest

import cleanup

HOUR_MS = 3600 * 1000
NOW = 1_800_000_000_000


REALM_ID = "beg-realm-uuid"


def event(user_id, client_uuid, op="CREATE", resource="USER", realm_id=REALM_ID):
    return {"operationType": op, "resourceType": resource, "resourcePath": f"users/{user_id}",
            "authDetails": {"clientId": client_uuid, "realmId": realm_id}}


class CreatorsFromAdminEventsTest(unittest.TestCase):
    def test_user_creation_by_realm_client_is_mapped(self):
        events = [event("u1", "c-uuid")]
        self.assertEqual(cleanup.creators_from_admin_events(events, REALM_ID), {"u1": "c-uuid"})

    def test_other_operations_and_resources_are_ignored(self):
        events = [event("u1", "c-uuid", op="UPDATE"),
                  event("u2", "c-uuid", resource="GROUP_MEMBERSHIP"),
                  {"operationType": "CREATE", "resourceType": "USER", "resourcePath": "users/u3/groups/g1",
                   "authDetails": {"clientId": "c-uuid", "realmId": REALM_ID}}]
        self.assertEqual(cleanup.creators_from_admin_events(events, REALM_ID), {})

    def test_creations_by_master_realm_admins_are_ignored(self):
        events = [event("u1", "master-client-uuid", realm_id="master-realm-uuid")]
        self.assertEqual(cleanup.creators_from_admin_events(events, REALM_ID), {})


class ResolveCreatorsTest(unittest.TestCase):
    """Admin-Events nennen in authDetails.clientId die interne UUID des Clients (Spike A 2026-11-25)."""

    def test_internal_client_uuid_is_translated_to_client_id(self):
        clients = [{"id": "dc98b1ce", "clientId": "beg-mr-7-crm"}]
        self.assertEqual(cleanup.resolve_creators({"u1": "dc98b1ce"}, clients), {"u1": "beg-mr-7-crm"})

    def test_uuid_of_deleted_client_is_marked_deleted(self):
        self.assertEqual(cleanup.resolve_creators({"u1": "gone-uuid"}, []), {"u1": cleanup.DELETED_CLIENT})


class SelectUsersTest(unittest.TestCase):
    def test_user_created_by_vanished_preview_is_deleted(self):
        users = [{"id": "u1", "username": "neu@test.de"}]
        self.assertEqual(cleanup.select_users_to_delete(users, {"u1": "beg-mr-7-crm"}, {"mr-12", "main"}, NOW),
                         ["u1"])

    def test_user_created_by_existing_preview_is_kept(self):
        users = [{"id": "u1", "username": "neu@test.de"}]
        self.assertEqual(cleanup.select_users_to_delete(users, {"u1": "beg-mr-12-crm"}, {"mr-12"}, NOW), [])

    def test_fixed_test_user_without_creation_event_is_kept(self):
        users = [{"id": "u1", "username": "anna.schmidt@test.de", "createdTimestamp": NOW - 100 * HOUR_MS}]
        self.assertEqual(cleanup.select_users_to_delete(users, {}, set(), NOW), [])

    def test_user_created_by_since_deleted_realm_client_is_deleted(self):
        # Preview weg -> ihr Client ist geloescht -> UUID nicht mehr aufloesbar
        users = [{"id": "u1", "username": "neu@test.de"}]
        self.assertEqual(cleanup.select_users_to_delete(users, {"u1": cleanup.DELETED_CLIENT}, {"mr-12"}, NOW),
                         ["u1"])

    def test_user_created_by_non_preview_client_is_kept(self):
        users = [{"id": "u1", "username": "x@test.de"}]
        self.assertEqual(cleanup.select_users_to_delete(users, {"u1": "admin-cli"}, set(), NOW), [])

    def test_e2e_user_older_than_24h_is_deleted(self):
        users = [{"id": "old", "username": "e2e-4711-ab12@e2e.invalid", "createdTimestamp": NOW - 25 * HOUR_MS},
                 {"id": "new", "username": "e2e-4712-cd34@e2e.invalid", "createdTimestamp": NOW - 1 * HOUR_MS}]
        self.assertEqual(cleanup.select_users_to_delete(users, {}, set(), NOW), ["old"])

    def test_service_account_user_is_never_deleted(self):
        users = [{"id": "sa", "username": "service-account-beg-mr-7-crm", "serviceAccountClientId": "beg-mr-7-crm"}]
        self.assertEqual(cleanup.select_users_to_delete(users, {"sa": "beg-mr-7-crm"}, set(), NOW), [])


class SelectClientsTest(unittest.TestCase):
    def test_client_of_vanished_preview_is_deleted(self):
        clients = [{"id": "c1", "clientId": "beg-mr-7-crm"}, {"id": "c2", "clientId": "beg-mr-7-portal"}]
        self.assertEqual(cleanup.select_clients_to_delete(clients, {"mr-12"}), ["c1", "c2"])

    def test_client_of_existing_preview_is_kept(self):
        clients = [{"id": "c1", "clientId": "beg-main-crm"}, {"id": "c2", "clientId": "beg-mr-12-portal"}]
        self.assertEqual(cleanup.select_clients_to_delete(clients, {"main", "mr-12"}), [])

    def test_static_and_builtin_clients_are_kept(self):
        clients = [{"id": "c1", "clientId": "beg-e2e-admin"}, {"id": "c2", "clientId": "account-console"},
                   {"id": "c3", "clientId": "realm-management"}]
        self.assertEqual(cleanup.select_clients_to_delete(clients, set()), [])


class PreviewIdsTest(unittest.TestCase):
    def test_preview_ids_from_namespace_names(self):
        names = ["beg-mr-12", "beg-main", "beg-previews", "keycloak", "beg-mr-3"]
        self.assertEqual(cleanup.preview_ids_from_namespaces(names), {"mr-12", "main", "mr-3"})


if __name__ == "__main__":
    unittest.main()
