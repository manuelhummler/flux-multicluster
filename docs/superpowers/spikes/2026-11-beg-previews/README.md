# Spikes für die beg-Previews (Plan Tasks 2 und 3)

Beide Spikes laufen erst, wenn die Keycloak-Instanz `beg` läuft (Plan Task 1 gepusht). Ergebnis jeweils
unten eintragen und in Spec §7 übernehmen.

## Spike A – Keycloak-Clients per `KeycloakOIDCClient` (Task 2)

Fragen:
1. Wird aus einem Client **ohne** `auth` ein öffentlicher Client mit PKCE (`publicClient: true`)?
2. In welcher Schreibweise vergibt `serviceAccountRoles` die `realm-management`-Rollen (Nutzerverwaltung)?
3. Löscht der Operator den Client in Keycloak, wenn die CR gelöscht wird?
4. Enthält das Token neuer Clients den Claim `roles` (Default-Scope `beg-roles` aus dem Realm-Import)?

Ablauf (nach Freigabe):

```powershell
kubectl -n keycloak apply -f docs/superpowers/spikes/2026-11-beg-previews/spike-clients.yaml
kubectl -n keycloak get keycloakoidcclients spike-portal spike-crm -o yaml
```

Prüfen in der Admin-Konsole `https://auth-beg.hummler.com/admin/master/console/#/beg/clients`
(Login mit dem Bootstrap-Admin aus `kubectl -n keycloak get secret beg-initial-admin`):
- `spike-portal`: Client authentication **aus**, PKCE S256?
- `spike-crm`: Service-Account-Rollen-Tab → `realm-management` manage-users usw.?
- Token holen und Nutzer anlegen:

```powershell
$secret = kubectl -n keycloak get secret spike-crm-client -o jsonpath='{.data.client-secret}' | % { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }
$tok = (Invoke-RestMethod -Method Post https://auth-beg.hummler.com/realms/beg/protocol/openid-connect/token -Body @{grant_type='client_credentials'; client_id='spike-crm'; client_secret=$secret}).access_token
Invoke-RestMethod -Method Post https://auth-beg.hummler.com/admin/realms/beg/users -Headers @{Authorization="Bearer $tok"} -ContentType 'application/json' -Body '{"username":"spike-user@e2e.invalid","enabled":true}'
```

Aufräumen und Frage 3:

```powershell
kubectl -n keycloak delete -f docs/superpowers/spikes/2026-11-beg-previews/spike-clients.yaml
```

Danach `spike-portal`/`spike-crm` in der Admin-Konsole nicht mehr vorhanden? Nutzer `spike-user@e2e.invalid` von Hand löschen.

**Ergebnis:** _(offen)_

## Spike B – Rootless-Image-Build auf dem karibik-Runner (Task 3)

Frage: Baut BuildKit rootless oder Buildah das Backend-Image (`source/Dockerfile`, nutzt `RUN --mount=type=cache`)
unter den Runner-Regeln (kein privileged, Namespace PSS baseline)?

Ablauf: Branch `spike/rootless-build` in beg-verwaltung mit nur diesen zwei Jobs in `.gitlab-ci.yml` pushen
(kein MR nötig), Logs prüfen.

```yaml
stages: [spike]

.registry_auth: &registry_auth |
  mkdir -p ~/.docker
  printf '{"auths":{"%s":{"auth":"%s"}}}' "$CI_REGISTRY" "$(printf '%s:%s' "$CI_REGISTRY_USER" "$CI_REGISTRY_PASSWORD" | base64 | tr -d '\n')" > ~/.docker/config.json

spike_buildkit:
  stage: spike
  tags: [karibik]
  image:
    name: moby/buildkit:v0.26.2-rootless
    entrypoint: [""]
  variables:
    BUILDKITD_FLAGS: --oci-worker-no-process-sandbox
    KUBERNETES_MEMORY_LIMIT: "6Gi"
  script:
    - *registry_auth
    - printf 'premiumRepoPass=%s\n' "$(cat /gradle-secrets/premiumRepoPass)" > /tmp/gradle-secret.properties
    - >
      buildctl-daemonless.sh build --frontend dockerfile.v0
      --local context=source --local dockerfile=source
      --output type=image,name=$CI_REGISTRY_IMAGE/ci/spike:buildkit-$CI_COMMIT_SHORT_SHA,push=true

spike_buildah:
  stage: spike
  tags: [karibik]
  image: quay.io/buildah/stable:v1.42
  variables:
    STORAGE_DRIVER: vfs
    BUILDAH_ISOLATION: chroot
    KUBERNETES_MEMORY_LIMIT: "6Gi"
  script:
    - buildah login -u "$CI_REGISTRY_USER" -p "$CI_REGISTRY_PASSWORD" "$CI_REGISTRY"
    - buildah build -t $CI_REGISTRY_IMAGE/ci/spike:buildah-$CI_COMMIT_SHORT_SHA source
    - buildah push $CI_REGISTRY_IMAGE/ci/spike:buildah-$CI_COMMIT_SHORT_SHA
```

Hinweise: Das Dockerfile kopiert heute `gradle.properties` aus dem Kontext (in der alten Pipeline per `cp` aus
`/root/.gradle`). Für den Spike reicht es, dass der Build bis zum Gradle-Schritt kommt; Jmix-premium-Zugriff
klärt Task 10 (Build-Secret). Image-Versionen vor dem Push auf aktuelle Tags prüfen.

**Ergebnis:** _(offen)_
