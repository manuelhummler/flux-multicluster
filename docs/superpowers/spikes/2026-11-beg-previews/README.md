# Spikes für die beg-Previews (Plan Tasks 2 und 3)

Beide Spikes laufen erst, wenn die Keycloak-Instanz `beg` läuft (Plan Task 1 gepusht). Ergebnis jeweils
unten eintragen und in Spec §7 übernehmen.

## Spike A – Keycloak-Clients per `KeycloakOIDCClient` (Task 2)

Fragen:
1. Wird aus einem Client **ohne** `auth` ein öffentlicher Client mit PKCE (`publicClient: true`)?
2. In welcher Schreibweise vergibt `serviceAccountRoles` die `realm-management`-Rollen (Nutzerverwaltung)?
3. Löscht der Operator den Client in Keycloak, wenn die CR gelöscht wird?
4. Enthält das Token neuer Clients den Claim `roles` (Default-Scope `beg-roles` aus dem Realm-Import)?

Ablauf per GitOps: Die Test-Clients liegen in `infrastructure/karibik/spikes/spike-clients.yaml` (Flux-Kustomization
`spikes`, prune). Anlegen = Push, Aufräumen = Eintrag aus `infrastructure/karibik/spikes/kustomization.yaml` entfernen
und pushen.

```powershell
kubectl -n keycloak get keycloakoidcclients spike-portal spike-crm -o yaml
```

Prüfen über die Admin-API mit dem Operator-Client aus Secret `beg-admin` (master-Realm, Service-Account; einen
Admin-Nutzer für die Konsole legt `bootstrapAdmin.service` nicht an):

```powershell
$id = kubectl -n keycloak get secret beg-admin -o jsonpath='{.data.client-id}' | % { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }
$sec = kubectl -n keycloak get secret beg-admin -o jsonpath='{.data.client-secret}' | % { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }
$adm = (Invoke-RestMethod -Method Post https://auth-beg.hummler.com/realms/master/protocol/openid-connect/token -Body @{grant_type='client_credentials'; client_id=$id; client_secret=$sec}).access_token
Invoke-RestMethod "https://auth-beg.hummler.com/admin/realms/beg/clients?clientId=spike-portal" -Headers @{Authorization="Bearer $adm"} | Select clientId, publicClient, attributes
Invoke-RestMethod "https://auth-beg.hummler.com/admin/realms/beg/client-scopes" -Headers @{Authorization="Bearer $adm"} | Select name
```

Erwartet zusätzlich (nach dem Realm-Setup-Job): Scopes `profile`, `email`, `roles`, `beg-roles` vorhanden; ein Token von
`spike-crm` enthält `sub`, `preferred_username`, `email` und `roles`.
- `spike-portal`: Client authentication **aus**, PKCE S256?
- `spike-crm`: Service-Account-Rollen-Tab → `realm-management` manage-users usw.?
- Token holen und Nutzer anlegen:

```powershell
$secret = kubectl -n keycloak get secret spike-crm-client -o jsonpath='{.data.client-secret}' | % { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }
$tok = (Invoke-RestMethod -Method Post https://auth-beg.hummler.com/realms/beg/protocol/openid-connect/token -Body @{grant_type='client_credentials'; client_id='spike-crm'; client_secret=$secret}).access_token
Invoke-RestMethod -Method Post https://auth-beg.hummler.com/admin/realms/beg/users -Headers @{Authorization="Bearer $tok"} -ContentType 'application/json' -Body '{"username":"spike-user@e2e.invalid","enabled":true}'
```

Aufräumen und Frage 3: `- spike-clients.yaml` aus `infrastructure/karibik/spikes/kustomization.yaml` entfernen,
pushen. Danach `spike-portal`/`spike-crm` per Admin-API nicht mehr vorhanden? Testnutzer `spike-user@e2e.invalid`
räumt der Aufräum-Job nicht ab (Client heißt nicht `beg-<id>-crm`) – von Hand löschen.

**Ergebnis (2026-11-25, Teil 1):**
1. Ja: ohne `auth` entsteht ein öffentlicher Client (`publicClient: true`, Standard-Flow). PKCE wird nicht erzwungen
   (`pkce.code.challenge.method` leer); das Portal nutzt PKCE S256 trotzdem clientseitig. Default-Scopes inkl.
   `beg-roles` werden geerbt.
2. `serviceAccountRoles` nimmt **nur Realm-Rollen**: `realm-management/manage-users`, `realm-management:manage-users`
   und `manage-users` → 400 „Cannot assign role … as it does not exist“; Realm-Rolle `portal-user` → ok.
   Lösung: zusammengesetzte Realm-Rolle `beg-preview-user-admin` (Realm-Setup-Job).
3. Realm-Rolle `beg-preview-user-admin` am Service-Account: ok (`HasErrors=False`).
4. **Neu:** Der Operator legt Clients **deaktiviert** an (`enabled: false`), wenn `client.enabled` fehlt – Token
   liefert dann `invalid_client`, obwohl das Secret stimmt. Alle Client-CRs brauchen `enabled: true`; die
   Admission-Policy erzwingt das für Preview-Clients.
5. **Neu:** Der Operator legt Clients mit `fullScopeAllowed: false` an (kein CRD-Feld dafür). Tokens enthalten dann
   nur gemappte Rollen: `roles` fehlte, `realm-management` leer, Admin-API → 401. Lösung: Scope-Mappings (Realm-Rollen
   + realm-management-Rechte) am Default-Scope `beg-roles` (Realm-Setup-Job, `beg-realm-setup-4`).
6. Nach `beg-realm-setup-4`: Token enthält `roles: [beg-preview-user-admin]` und realm-management manage/view/query-users,
   query-groups – Admin-API trotzdem 401, auch für den importierten `beg-e2e-admin`. **Ursache:** Keycloak 26.x
   verlangt für die Admin-API die Audience aus Scope `kc-admin-api` (Mapper `oidc-admin-api-audience-mapper`); nur
   das Master-Token hatte sie. Lösung: `kc-admin-api` als Realm-Default und Default-Scope aller vorhandenen
   Service-Account-Clients (`beg-realm-setup-5`). **Achtung für Dev/Prod:** Nach einem Keycloak-Update auf diese
   Version braucht auch der CRM-Client dort `kc-admin-api` als Default-Scope, sonst scheitert `KeyCloakService`.
7. Nach `beg-realm-setup-5`: Token `aud` = `…/admin/realms/beg` + `realm-management`; `spike-crm` legt Nutzer an (201),
   sucht (200), liest Gruppen (200) – genau, was `KeyCloakService` braucht. Aufräum-Client liest Admin-Events und
   löscht (204).
8. **Neu:** Admin-Events nennen in `authDetails.clientId` die **interne UUID** des Clients, nicht `beg-mr-7-crm`.
   Aufräum-Job übersetzt über die Client-Liste; ist der Client gelöscht (Preview abgebaut), gilt der Nutzer als
   verwaist – sofern der anlegende Client aus Realm `beg` stammt (`authDetails.realmId == realmId`).
9. Frage 3: ja – nach Entfernen aus der Spikes-Kustomization (61a2ccc) prunt Flux CRs und Secret, der Operator löscht
   beide Clients in Keycloak (Admin-API: 0 Treffer).

**Spike A abgeschlossen (2026-11-25).** Konsequenzen für die Preview-Clients: `enabled: true` Pflicht,
`serviceAccountRoles: [beg-preview-user-admin]`, Rollen/Audience kommen über Default-Scopes `beg-roles` und
`kc-admin-api` (Realm-Setup-Job), Aufräumen über Admin-Events mit Client-UUIDs.

## Spike B – Rootless-Image-Build auf dem karibik-Runner (Task 3)

Frage: Baut BuildKit rootless oder Buildah das Backend-Image (`source/Dockerfile`, nutzt `RUN --mount=type=cache`)
unter den Runner-Regeln (kein privileged, Namespace PSS baseline)?

Ablauf: Branch `spike/rootless-build` in beg-verwaltung (Worktree `D:/Workspace/beg-project/beg-verwaltung-spike`,
von `origin/main`), dessen `.gitlab-ci.yml` nur drei Spike-Jobs enthält (kein MR nötig, `workflow:rules` nur für
diesen Branch):

- `spike_probe` (alpine): Capabilities, Seccomp, AppArmor-Profil, User-Namespaces des Job-Pods
- `spike_buildkit`: `moby/buildkit:v0.35.1-rootless`, `buildctl-daemonless.sh` mit
  `BUILDKITD_FLAGS=--oci-worker-no-process-sandbox`, pusht `ci/spike:buildkit-<sha>`
- `spike_buildah`: `quay.io/buildah/stable:v1.46.1`, `vfs` + `--isolation chroot`, pusht `ci/spike:buildah-<sha>`

Beide Builds bauen `source/` (Backend, `RUN --mount=type=cache`). `premiumRepoPass` kommt aus `/gradle-secrets` des
Runners und wird nur für die Build-Stage an `source/gradle.properties` angehängt.

**Ergebnis:** _(offen)_
