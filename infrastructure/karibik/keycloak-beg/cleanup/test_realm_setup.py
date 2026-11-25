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


class UserProfileTest(unittest.TestCase):
    def test_policy_added_when_missing(self):
        profile = {"attributes": [{"name": "username"}]}
        changed, new = realm_setup.profile_with_admin_edit(profile)
        self.assertTrue(changed)
        self.assertEqual(new["unmanagedAttributePolicy"], "ADMIN_EDIT")
        self.assertEqual(new["attributes"], [{"name": "username"}])

    def test_unchanged_when_policy_already_set(self):
        profile = {"attributes": [], "unmanagedAttributePolicy": "ADMIN_EDIT"}
        changed, _ = realm_setup.profile_with_admin_edit(profile)
        self.assertFalse(changed)

    def test_stricter_policy_is_widened_to_admin_edit(self):
        profile = {"attributes": [], "unmanagedAttributePolicy": "ADMIN_VIEW"}
        changed, new = realm_setup.profile_with_admin_edit(profile)
        self.assertTrue(changed)
        self.assertEqual(new["unmanagedAttributePolicy"], "ADMIN_EDIT")

    def test_more_permissive_policy_is_kept(self):
        profile = {"attributes": [], "unmanagedAttributePolicy": "ENABLED"}
        changed, _ = realm_setup.profile_with_admin_edit(profile)
        self.assertFalse(changed)


if __name__ == "__main__":
    unittest.main()
