# BEG MR-Previews Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pro offenem MR in `einfach-genossenschaft/beg-verwaltung` (und dauerhaft für `main`) eine öffentliche, per Flux Operator verwaltete Preview auf karibik, gegen die `test_e2e` Playwright ausführt und die nach Merge/Close verschwindet.

**Architecture:** Plattform (flux-multicluster): Keycloak-Instanz `beg`, ResourceSetInputProvider (`GitLabMergeRequest` + `GitLabBranch main`), ResourceSet mit eingeschränkten ServiceAccounts und ValidatingAdmissionPolicy. App-Vorlage (beg-verwaltung `deployment/preview/`): Backend, Portal, Postgres, GreenMail, Ingress, Keycloak-Clients, Demo-Daten-Restore. CI baut Images rootless pro Commit und wartet in `test_e2e` auf die Preview mit dem eigenen Commit.

**Tech Stack:** Flux Operator v0.54.1 (ResourceSet), Flux 2.9, Keycloak Operator 26.8.2 (`KeycloakOIDCClient` v2alpha1), Kustomize, Cilium, cert-manager, CNPG, GitLab CI (Kubernetes-Executor, karibik-Runner), BuildKit rootless oder Buildah, crane, Playwright 1.61.1, Jmix 3 / Spring Boot 4 / Java 25.

**Spec:** `docs/superpowers/specs/2026-11-25-beg-mr-previews-design.md` (im flux-multicluster-Repo)

## Global Constraints

- **Kein Push, kein Commit durch den Agenten.** Jede Task endet mit „Checkpoint: Nutzer prüft und committet“. Cluster-Änderungen entstehen nur durch den Push des Nutzers; der Agent darf lesend `kubectl --context karibik get/describe/logs` nutzen und `kubectl apply --dry-run=server`. Echte `kubectl apply` nur in den Spike-Tasks und nur nach Freigabe des Nutzers.
- Kein `privileged`, kein DinD, kein Docker-Socket.
- Hosts: `beg-mr-<iid>.hummler.com`, `beg-main.hummler.com`, Keycloak `auth-beg.hummler.com`.
- Keycloak: Instanz `beg` im Namespace `keycloak`, Realm `beg`, Feature `client-admin-api:v2`.
- Client-Namen: `beg-mr-<id>-crm`, `beg-mr-<id>-portal` (bzw. `beg-main-…`), statisch `beg-e2e-admin`.
- Preview-Namespace: `beg-mr-<iid>` bzw. `beg-main`.
- Demo-Daten-Image: `registry.gitlab.com/einfach-genossenschaft/beg-verwaltung/demo-data:<YYYYMMDD_HHMMSS>` mit `/data/beg.sql` und `/data/jmix.zip`; Restore mit `postgres:17` (psql ≥ 17.6, `ON_ERROR_STOP=on`).
- Commit-Kennung: Env `INFO_PREVIEW_COMMIT=<sha>` + `MANAGEMENT_INFO_ENV_ENABLED=true` → `GET /api/actuator/info` liefert `{"preview":{"commit":"<sha>"}}`.
- `filter.limit: 8`, Opt-out-Label `preview::off`, keine Fork-MRs.
- documents4j und Elasticsearch abgeschaltet (Env wie in `docker/e2e/docker-compose.e2e.yml`).
- Doku auf Deutsch in `docs/clusters/gitops-karibik.md` (neuer Abschnitt 4j); YAML-Kommentare ohne Umlaute wie im Repo üblich.

## Review Focus

1. **Neuer Push während eines E2E-Laufs** – der Lauf muss scheitern oder neu warten, nie gegen gemischte Stände grün werden → Task 11 prüft vor und nach der Suite den Commit.
2. **MR-Manifest versucht, einen Client in Instanz `keycloak`/`pro-makler` oder Realm ≠ `beg` anzulegen** – muss abgelehnt werden → Task 8 testet die Policy mit `--dry-run=server`.
3. **Image zum Commit fehlt noch** (CI langsamer als Flux) – Preview bleibt in `ImagePullBackOff` und erholt sich ohne Eingriff → Task 5 setzt `imagePullPolicy: IfNotPresent` und keine Rollout-Deadline, die die Kustomization dauerhaft rot macht; Task 11 wartet mit Timeout.
4. **Dump-Restore scheitert** (z.B. Image-Version zu alt für `\restrict`) – Backend darf nicht mit leerer DB starten → Task 5: Restore als Init-Container mit `ON_ERROR_STOP`, Backend startet erst danach.
5. **MR wird geschlossen, während Keycloak `beg` nicht erreichbar ist** – Client bleibt sonst in Keycloak liegen → Task 9: Aufräum-CronJob löscht auch verwaiste Clients `beg-mr-*` ohne Namespace.

---

## Phase 0 – Plattform-Grundlage und Spikes

### Task 1: Keycloak-Instanz `beg` mit Realm-Import

**Files (flux-multicluster):**
- Create: `infrastructure/karibik/keycloak-beg/kustomization.yaml`, `keycloak.yaml`, `realm-import.yaml`, `ingress.yaml`, `networkPolicy.yaml`, `admin-encrypted.yaml`, `realm-users-encrypted.yaml`
- Modify: `infrastructure/karibik/cloudnative-pg-cluster/` (Rolle + Database `beg-keycloak`, Secret `secrets/beg-keycloak-encrypted.env`) nach dem Muster `keycloak`/`promakler-keycloak`
- Modify: `clusters/karibik/infrastructure.yaml` (Kustomization `keycloak-beg`, dependsOn `keycloak-operator`, `cloudnative-pg-cluster`, `cluster-issuer`)

**Interfaces:**
- Produces: Keycloak-CR `beg` (ns `keycloak`), Realm `beg`, Client `beg-e2e-admin` (vertraulich, Service-Account mit `realm-management` manage-users/view-users/query-users), Secret `beg-e2e-admin` (Key `client-secret`), Operator-Admin-Secret `beg-admin` (`client-id`, `client-secret`), Issuer `https://auth-beg.hummler.com/realms/beg`.

- [ ] **Step 1:** Keycloak-CR `beg` nach dem Muster der Live-CR `pro-makler` (Ressourcen, `unsupported.podTemplate`-SecurityContext, `proxy.headers: xforwarded`, `http.httpEnabled: true`, Ingress/NetworkPolicy des Operators aus), `hostname: https://auth-beg.hummler.com`, `features.enabled: [client-admin-api:v2]`, `bootstrapAdmin.service.secret: beg-admin`.
- [ ] **Step 2:** `KeycloakRealmImport` `beg` aus `beg-verwaltung/docker/e2e/keycloak/realm-beg.json` ableiten: Redirects auf `localhost` entfernen, Client `beg-e2e-admin` ergänzen, feste Testnutzer ergänzen (Liste aus dem Demo-Dump: `SELECT username, email FROM beg_user` gegen eine lokale Kopie bzw. vom Nutzer liefern lassen), Passwörter als Platzhalter `${...}` mit Werten aus `realm-users-encrypted.yaml` (Flux-`postBuild.substituteFrom`).
- [ ] **Step 3: Verifizieren** – `kubectl kustomize --load-restrictor LoadRestrictionsNone infrastructure/karibik/keycloak-beg` baut; `kubectl apply --dry-run=server -k …` (nach `sops -d` in eine Temp-Kopie) ohne Fehler.
- [ ] **Step 4: Checkpoint** – Nutzer prüft, trägt Passwörter per `sops` ein, committet, pusht. Danach Agent: `kubectl -n keycloak get keycloak beg` → `Ready=True`, `curl -fsS https://auth-beg.hummler.com/realms/beg/.well-known/openid-configuration` → 200.

### Task 2: Spike Keycloak-Clients

**Files (flux-multicluster):**
- Create: `docs/superpowers/spikes/2026-11-keycloak-clients/{portal-public.yaml,crm-confidential.yaml,README.md}`

**Interfaces:**
- Produces: im README festgehalten: (a) YAML-Form eines öffentlichen Portal-Clients (oder „geht nicht“), (b) exaktes Format von `serviceAccountRoles` für Nutzerverwaltung, (c) ob Löschen der CR den Client entfernt.

- [ ] **Step 1:** Zwei Test-CRs `spike-portal` (`loginFlows: [STANDARD]`, ohne `auth`; zweite Variante mit `auth.method` passend zur CRD-Validierung) und `spike-crm` (`STANDARD`, `SERVICE_ACCOUNT`, `auth.method: client-secret`, `secretRef`, `serviceAccountRoles: ["realm-management/manage-users"]`) gegen `keycloakCRName: beg`, `realm: beg`.
- [ ] **Step 2: Nach Freigabe des Nutzers** `kubectl apply`, dann prüfen: Status-Conditions, im Keycloak-Admin-UI bzw. per Admin-API `GET /admin/realms/beg/clients?clientId=spike-portal` → `publicClient`, PKCE-Attribut; Token per `client_credentials` für `spike-crm` holen und `POST /admin/realms/beg/users` → 201.
- [ ] **Step 3:** `kubectl delete` der CRs → Clients in Keycloak weg (Admin-API 0 Treffer).
- [ ] **Step 4:** Ergebnis ins README und in Spec §7; **Gate:** geht der öffentliche Client nicht, Rückfrage an den Nutzer mit den Fallbacks aus Spec §7.1, bevor Task 6 beginnt.

### Task 3: Spike Rootless-Build

**Files (beg-verwaltung, Branch `spike/rootless-build`, nicht mergen):**
- Modify: `.gitlab-ci.yml` (nur ein Job `spike_build`, Tag `karibik`)

- [ ] **Step 1:** Job A: `moby/buildkit:<aktuelle rootless-Version>` mit `buildctl-daemonless.sh build --frontend dockerfile.v0 --local context=source --local dockerfile=source --output type=image,name=$CI_REGISTRY_IMAGE/ci/spike:$CI_COMMIT_SHA,push=true`, `BUILDKITD_FLAGS=--oci-worker-no-process-sandbox`, Registry-Auth über `~/.docker/config.json` aus `CI_REGISTRY_*`.
- [ ] **Step 2:** Job B: `quay.io/buildah/stable` mit `buildah build --isolation chroot --storage-driver vfs` + `buildah push`.
- [ ] **Step 3:** Nutzer pusht den Branch (kein MR nötig), Agent liest die Job-Logs (Nutzer liefert sie, falls die GitLab-MCP nicht verbunden ist).
- [ ] **Step 4: Gate:** Werkzeug festlegen (bevorzugt BuildKit wegen `RUN --mount=type=cache`). Scheitern beide an AppArmor/Seccomp/User-Namespaces unter PSS baseline: Rückfrage an den Nutzer (Spec §7.3), nicht eigenmächtig Runner-Regeln lockern.

## Phase 1 – App-Vorlage (beg-verwaltung)

### Task 4: Demo-Daten-Image

**Files (beg-verwaltung):**
- Create: `deployment/demo-data/Dockerfile` (`FROM busybox:1.37.0`, `COPY beg.sql jmix.zip /data/`), `deployment/demo-data/README.md`, Eintrag `deployment/demo-data/*.sql`, `*.zip` in `.gitignore`

- [ ] **Step 1:** README mit den Befehlen (PowerShell + bash): Dateien aus `psql_dump/backups/<stand>/` holen (`scp hummler@46.229.21.51:…`), `docker build -t registry.gitlab.com/einfach-genossenschaft/beg-verwaltung/demo-data:<stand> deployment/demo-data`, `docker push …`; danach `DEMO_DATA_VERSION` in `deployment/preview/app/kustomization.yaml` anheben.
- [ ] **Step 2: Checkpoint** – Nutzer baut und pusht `demo-data:20261111_104103`, prüft `docker run --rm …/demo-data:20261111_104103 ls -la /data` → beide Dateien.

### Task 5: Preview-App-Manifeste

**Files (beg-verwaltung):**
- Create: `deployment/preview/app/{kustomization.yaml,db.yaml,backend.yaml,portal.yaml,mail.yaml,ingress.yaml,networkPolicy.yaml}`, `deployment/preview/README.md`

**Interfaces:**
- Consumes: Substitutionsvariablen (von Task 8 gesetzt): `PREVIEW_ID` (`mr-<iid>` oder `main`), `PREVIEW_SHA`, `PREVIEW_HOST`; Secrets im Namespace (von Task 8 kopiert): `registry-pull` (dockerconfigjson), `crm-client` (Key `client-secret`).
- Produces: Services `backend:8082`, `portal:80`, `mail:3025/8080`, `db:5432`; Ingress-Host `${PREVIEW_HOST}`; `/api/actuator/info` mit `preview.commit`.

- [ ] **Step 1: DB** – Deployment `db` (`postgres:17`, `emptyDir` für Daten, Annotation `preview/commit: ${PREVIEW_SHA}` → neuer Pod pro Commit), Init-Container `demo-data` (Image `demo-data:${DEMO_DATA_VERSION}`) kopiert `/data/beg.sql` in ein geteiltes `emptyDir`; Restore per `/docker-entrypoint-initdb.d/` (Postgres-Image spielt `*.sql` beim ersten Start ein; `ON_ERROR_STOP` über ein Wrapper-Skript `00-restore.sh` mit `psql -v ON_ERROR_STOP=1`). Readiness `pg_isready`.
- [ ] **Step 2: Backend** – Deployment `backend` (`registry.gitlab.com/einfach-genossenschaft/beg-verwaltung/ci/backend:${PREVIEW_SHA}`, `imagePullSecrets: registry-pull`), Init-Container 1 wartet auf `db` (`pg_isready` mit Retry, max. 10 min) **und** prüft, dass die Tabelle `beg_user` existiert (Restore fertig); Init-Container 2 entpackt `jmix.zip` nach `/app` (Volume `emptyDir` auf `/app/.jmix`). Env aus `docker-compose.e2e.yml` übernehmen, ersetzt: `MAIN_DATASOURCE_URL=jdbc:postgresql://db:5432/beg`, Issuer/`SPRING_SECURITY_OAUTH2_*` auf `https://auth-beg.hummler.com/realms/beg`, Client-ID `beg-${PREVIEW_ID}-crm`, Secret aus `crm-client`, `KEYCLOAKSERVICE_CONFIG_ADMINURL=https://auth-beg.hummler.com/admin/realms/beg`, `KEYCLOAKSERVICE_CONFIG_PREVIEWID=${PREVIEW_ID}`, `JMIX_CORS_ALLOWED_ORIGINS=https://${PREVIEW_HOST}`, `BEG_MEMBER_PORTAL_URL=https://${PREVIEW_HOST}`, `INFO_PREVIEW_COMMIT=${PREVIEW_SHA}`, `MANAGEMENT_INFO_ENV_ENABLED=true`, `SERVER_FORWARD_HEADERS_STRATEGY=framework`. Ressourcen: Request 1 CPU/2Gi, Limit 2 CPU/3Gi. Readiness `/api/actuator/health`.
- [ ] **Step 3: Portal, Mail** – Portal (`ci/portal:${PREVIEW_SHA}`, Env `API_BASE_URL=https://${PREVIEW_HOST}/api`, `KEYCLOAK_URL=https://auth-beg.hummler.com`, `KEYCLOAK_REALM=beg`, `KEYCLOAK_CLIENT_ID=beg-${PREVIEW_ID}-portal`), GreenMail `greenmail/standalone:2.1.0` mit den Opts aus dem E2E-Stack.
- [ ] **Step 4: Ingress + Netz** – Traefik-Ingress `${PREVIEW_HOST}`: `/api` und die Vaadin-CRM-Pfade → `backend`, Rest → `portal` (genaue Pfadliste aus `SERVER_SERVLET_CONTEXTPATH`/Vaadin-Routen ermitteln); `cert-manager.io/cluster-issuer: letsencrypt-prod`. Cilium: default-deny, Traefik → backend/portal, backend → db/mail, backend → `world:443` (Keycloak über öffentliche URL), Init-Container-Pulls brauchen keine Regel.
- [ ] **Step 5: Verifizieren** – `flux envsubst --strict` (oder `envsubst`) mit `PREVIEW_ID=mr-1 PREVIEW_SHA=$(git rev-parse HEAD) PREVIEW_HOST=beg-mr-1.hummler.com DEMO_DATA_VERSION=20261111_104103` über `kubectl kustomize deployment/preview/app` → valides YAML; `kubectl apply --dry-run=server -n default -f -` ohne Schemafehler (Namespace-Feld ignorieren).
- [ ] **Step 6: Checkpoint** – Nutzer prüft und committet (noch kein Effekt im Cluster).

### Task 6: Preview-Keycloak-Clients

**Files (beg-verwaltung):**
- Create: `deployment/preview/keycloak/{kustomization.yaml,crm-client.yaml,portal-client.yaml}`

**Interfaces:**
- Consumes: Spike-Ergebnis Task 2; Secret `beg-${PREVIEW_ID}-crm-client` im Namespace `keycloak` (von Task 8 erzeugt, Label `operator.keycloak.org/kind: KeycloakOIDCClient`).
- Produces: `KeycloakOIDCClient` `beg-${PREVIEW_ID}-crm` und `beg-${PREVIEW_ID}-portal`.

- [ ] **Step 1:** CRM-Client: `keycloakCRName: beg`, `realm: beg`, `loginFlows: [STANDARD, SERVICE_ACCOUNT]`, `redirectUris: ["https://${PREVIEW_HOST}/*"]`, `webOrigins: ["+"]`, `auth: {method: client-secret, secretRef: {name: beg-${PREVIEW_ID}-crm-client, key: client-secret}}`, `serviceAccountRoles` im Format aus Task 2 (manage-users, view-users, query-users, query-groups, view-realm falls `getGroupIdByName` es braucht).
- [ ] **Step 2:** Portal-Client in der Form aus Task 2.
- [ ] **Step 3: Verifizieren** – Build mit Substitution wie Task 5; nach Task 8 zusätzlich Policy-Test (Task 8 Step 4).
- [ ] **Step 4: Checkpoint** – Nutzer prüft und committet.

### Task 7: App-Änderung – Preview-Attribut an Keycloak-Nutzern

**Files (beg-verwaltung):**
- Modify: `source/src/main/java/de/begpaf/mitgliederverwaltung/service/keycloak/KeyCloakUser.java` (Feld `public Map<String, List<String>> attributes;` mit `@JsonInclude(JsonInclude.Include.NON_NULL)`)
- Modify: `source/src/main/java/de/begpaf/mitgliederverwaltung/service/keycloak/KeyCloakSettings.java` (Property `previewId`, Getter/Setter → `keycloakservice.config.preview-id`)
- Modify: `source/src/main/java/de/begpaf/mitgliederverwaltung/service/keycloak/KeyCloakService.java` (package-private `String buildCreateUserPayload(KeyCloakUser user)`, von `createUser` genutzt)
- Test: `source/src/test/java/de/begpaf/mitgliederverwaltung/service/keycloak/KeyCloakServiceTest.java`

- [ ] **Step 1: Failing tests** (reines JUnit, ohne Spring-Kontext; `KeyCloakSettings` per `new` + Setter in den Service gesetzt):
  - `buildCreateUserPayload_withPreviewId_addsPreviewAttribute` → JSON enthält `"attributes":{"preview":["mr-12"]}`
  - `buildCreateUserPayload_withoutPreviewId_hasNoAttributes` (null und `""`) → JSON enthält kein `"attributes"`
  - `buildCreateUserPayload_keepsExistingAttributes` → vorhandenes Attribut bleibt, `preview` kommt dazu
- [ ] **Step 2:** `cd source && ./gradlew --no-daemon test --tests '*KeyCloakServiceTest'` → FAIL (Methode fehlt).
- [ ] **Step 3:** Implementieren; `createUser` serialisiert über `buildCreateUserPayload`.
- [ ] **Step 4:** Test erneut → PASS; danach volle Suite `./gradlew --no-daemon --no-build-cache test` grün.
- [ ] **Step 5: Checkpoint** – Nutzer prüft und committet (eigener MR möglich, unabhängig vom Rest).

## Phase 2 – Plattform-Steuerung (flux-multicluster)

### Task 8: InputProvider, ResourceSet, RBAC, Policy

**Files (flux-multicluster):**
- Create: `infrastructure/karibik/beg-previews/{kustomization.yaml,namespace.yaml,gitlab-token-encrypted.yaml,registry-pull-encrypted.yaml,deploy-key-encrypted.yaml,input-providers.yaml,resourceset.yaml,rbac.yaml,admission-policy.yaml,client-secret-job.yaml,receiver.yaml}`
- Modify: `clusters/karibik/infrastructure.yaml` (Kustomization `beg-previews`, dependsOn `keycloak-beg`, Decryption sops)

**Interfaces:**
- Consumes: Task 1 (Keycloak `beg`), Task 5/6 (Pfade `deployment/preview/app` und `deployment/preview/keycloak`).
- Produces: pro Input `id`: Namespace `beg-<id>`, GitRepository `beg-<id>` (SSH-Deploy-Key read-only, `ref.commit: <sha>`, `ignore` alles außer `/deployment`), Kustomization `beg-<id>-app` (targetNamespace `beg-<id>`, SA `beg-preview-app`), Kustomization `beg-<id>-keycloak` (targetNamespace `keycloak`, SA `beg-preview-keycloak`), Secrets `registry-pull` und `crm-client` im Preview-Namespace (copyFrom), Secret `beg-<id>-crm-client` in `keycloak`.

- [ ] **Step 1: InputProvider** – `beg-merge-requests` (`type: GitLabMergeRequest`, `url: https://gitlab.com/einfach-genossenschaft/beg-verwaltung`, `secretRef: gitlab-token` mit `username`/`password` = Project Access Token `read_api`, `filter.limit: 8`, `skip.labels: ["preview::off"]`, `reconcileEvery: 2m`, `defaultValues: {prefix: "mr-"}`), `beg-main` (`type: GitLabBranch`, `filter.includeBranch: "^main$"`, `defaultValues: {prefix: ""}`). Einheitliche Preview-ID im Template: `<< inputs.prefix >><< inputs.id >>` für MRs, für main der feste Wert `main` (über `defaultValues` bzw. eigenes ResourceSet – Builder wählt die einfachere Variante nach `flux-operator build resourceset`). Fork-MRs: im GitRepository-Fetch scheitern sie (SHA nicht im Upstream) – zusätzlich `includeBranch` nicht nötig; Verhalten im README dokumentieren.
- [ ] **Step 2: Client-Secret-Generator** – ResourceSet-Step 1 legt Secret `beg-<id>-crm-client` (ns `keycloak`, Label `operator.keycloak.org/kind: KeycloakOIDCClient`, ohne `data`) und einen Job `beg-<id>-client-secret` (ns `beg-previews`, Image `bitnami/kubectl`-Ersatz `registry.k8s.io/kubectl:<version>`) an, der `client-secret` nur setzt, wenn leer (`kubectl patch … --type merge` mit `openssl rand -hex 32` bzw. `/dev/urandom`). Step 2 kopiert es per `fluxcd.controlplane.io/copyFrom: "keycloak/beg-<id>-crm-client"` als `crm-client` in den Preview-Namespace; Step 3 die Kustomizations. SA des Jobs darf nur `get/patch` auf Secrets mit Namen `beg-*-crm-client` in `keycloak` (RBAC `resourceNames` geht nicht mit Wildcards → Rolle auf Secrets in `keycloak` beschränkt plus Admission-Policy, die dem Job-SA nur Secrets mit Präfix `beg-` erlaubt).
- [ ] **Step 3: RBAC** – SA `beg-previews` (ResourceSet): Namespaces anlegen/löschen, Flux-Objekte in `beg-previews`, Secrets lesen in `keycloak`/`beg-previews`. SA `beg-preview-app`: RoleBinding `admin`-ähnlich nur im jeweiligen Preview-Namespace (vom ResourceSet pro Preview angelegt). SA `beg-preview-keycloak`: Role in `keycloak` nur für `keycloakoidcclients`.
- [ ] **Step 4: ValidatingAdmissionPolicy** `beg-preview-keycloak-clients` (match: `keycloakoidcclients`, `request.userInfo.username == 'system:serviceaccount:beg-previews:beg-preview-keycloak'`): `spec.keycloakCRName == 'beg'`, `spec.realm == 'beg'`, `metadata.name.startsWith('beg-')`, alle `redirectUris` matchen `^https://beg-[a-z0-9-]+\.hummler\.com/`, `serviceAccountRoles` ⊆ Allowlist aus Task 6. **Test:** `kubectl apply --dry-run=server --as=system:serviceaccount:beg-previews:beg-preview-keycloak` mit (a) gültigem Client → OK, (b) `keycloakCRName: keycloak` → abgelehnt, (c) `realm: hopps` → abgelehnt, (d) Redirect `https://evil.example.com/*` → abgelehnt. (Erst nach Push der Policy möglich → Teil des Checkpoints.)
- [ ] **Step 5: Receiver** (optional, empfohlen) – Flux `Receiver` `type: gitlab` auf beide InputProvider; URL für den GitLab-Webhook im README.
- [ ] **Step 6: Verifizieren lokal** – `kubectl kustomize` baut; wenn `flux-operator` CLI vorhanden: `flux-operator build resourceset -f resourceset.yaml --inputs-from test-inputs.yaml` mit Beispiel-Inputs `{id: "12", sha: "<sha>", branch: "feat/x"}` → erwartete Objektliste.
- [ ] **Step 7: Checkpoint** – Nutzer legt Project Access Token + Deploy Token + Deploy Key an, trägt sie per `sops` ein, prüft, committet, pusht. Danach Agent: Policy-Tests aus Step 4, `kubectl get resourceset -n beg-previews`, Test-MR des Nutzers → Namespace `beg-mr-<iid>` erscheint; nach Schließen des Test-MR verschwinden Namespace und Clients (Admin-API prüfen).

### Task 9: Aufräum-CronJob für Keycloak-Nutzer und verwaiste Clients

**Files (flux-multicluster):**
- Create: `infrastructure/karibik/beg-previews/cleanup-cronjob.yaml`, `cleanup/cleanup.sh` (per `configMapGenerator`)

- [ ] **Step 1:** Skript (POSIX sh, `curl` + `jq`): Token via `client_credentials` mit `beg-e2e-admin`; `GET /admin/realms/beg/users?q=preview:` paginiert; löscht Nutzer, deren `preview`-Wert keinen Namespace `beg-<wert>` hat (Namensliste per Kubernetes-API, SA darf nur `list namespaces`); löscht Nutzer `e2e-*` mit `createdTimestamp` älter 24 h; löscht Clients `beg-mr-*`, zu denen kein Namespace existiert. Trockenlauf-Modus `DRY_RUN=1` gibt nur aus.
- [ ] **Step 2: Test** – Skript lokal gegen `auth-beg.hummler.com` mit `DRY_RUN=1` (Nutzer liefert das Secret oder führt aus) → listet erwartete Kandidaten, löscht nichts.
- [ ] **Step 3:** CronJob stündlich, PSS restricted, Egress nur `world:443` + kube-apiserver.
- [ ] **Step 4: Checkpoint** – Nutzer prüft und committet.

## Phase 3 – CI (beg-verwaltung)

### Task 10: Rootless Image-Builds mit Commit-Tags

**Files (beg-verwaltung):**
- Modify: `.gitlab-ci.yml` (`.image_refs`, `image_backend`, `image_portal`, `.build_backend`, `build_backend_latest`, `build_backend_tags`, `.build_frontend`, `build_frontend_latest`, `build_frontend_tags`)
- Modify: `source/Dockerfile` (Jmix-Zugangsdaten als BuildKit-Secret statt kopierter `gradle.properties`)

**Interfaces:**
- Consumes: Werkzeug aus Task 3; Runner-Secret `/gradle-secrets/premiumRepoPass` (4i).
- Produces: `ci/backend:<CI_COMMIT_SHA>`, `ci/portal:<CI_COMMIT_SHA>` für jede MR-/main-Pipeline (Tree-Hash-Reuse per `crane copy` statt Neubau).

- [ ] **Step 1:** Gemeinsame Vorlage `.rootless_build` (Tag `karibik`, Image aus Task 3, Registry-Auth); `crane` für `manifest`-Prüfung und Umtaggen.
- [ ] **Step 2:** `source/Dockerfile`: `RUN --mount=type=secret,id=gradle_props,target=/root/.gradle/gradle.properties …` statt `COPY` der Datei; CI übergibt `premiumRepoUser` (aus `source/gradle.properties`) + `premiumRepoPass` (aus `/gradle-secrets`) als Secret-Datei. Lokaler `docker build` bleibt möglich (README-Zeile mit `--secret`).
- [ ] **Step 3:** `image_backend`/`image_portal`: wenn Tree-Hash-Image existiert → `crane copy …:<treehash> …:<CI_COMMIT_SHA>`; sonst bauen und beide Tags pushen. `resource_group` entfernen.
- [ ] **Step 4:** `build_*_latest`/`build_*_tags` auf dieselbe Vorlage (Retag per `crane copy`, Tag-Builds bauen mit `APP_VERSION`).
- [ ] **Step 5: Verifizieren** – MR-Pipeline des Nutzers: beide Image-Jobs grün, `crane ls …/ci/backend` enthält `<CI_COMMIT_SHA>`; zweiter Push ohne Änderung in `source/` → Log „reusing“, kein Build.
- [ ] **Step 6: Checkpoint** – Nutzer prüft und committet.

### Task 11: E2E gegen die Preview

**Files (beg-verwaltung):**
- Modify: `.gitlab-ci.yml` (`test_e2e`)
- Create: `scripts/ci/wait-for-preview.sh`
- Modify: `tests/e2e/auth.setup.ts`, `tests/e2e/playwright.config.ts`, `tests/e2e/fixtures/*` (Testnutzer-Anlage), betroffene Specs unter `tests/e2e/{public,authenticated,processes}`
- Create: `tests/e2e/fixtures/keycloak-admin.ts`

**Interfaces:**
- Consumes: Preview-URL `https://beg-mr-$CI_MERGE_REQUEST_IID.hummler.com` bzw. `https://beg-main.hummler.com`; CI-Variable `BEG_E2E_ADMIN_SECRET` (maskiert) für Client `beg-e2e-admin`.
- Produces: `createE2eUser(): Promise<{username, password}>`, `deleteE2eUser(username): Promise<void>` in `keycloak-admin.ts`.

- [ ] **Step 1:** `wait-for-preview.sh <base-url> <sha> <timeout-s>`: pollt `<base-url>/api/actuator/info` bis `.preview.commit == <sha>` und `/api/actuator/health` `UP`, Exit 1 nach Timeout (1200 s) mit letzter Antwort im Log. Test lokal gegen eine nicht existente URL → Exit 1 nach kurzem Timeout; gegen laufende Preview → Exit 0.
- [ ] **Step 2:** `keycloak-admin.ts` + Setup: legt pro Lauf `e2e-${CI_PIPELINE_ID}-<random>@e2e.invalid` mit Passwort an (Realm `beg`, `emailVerified: true`, keine Required Actions), Teardown löscht. `auth.setup.ts` nutzt diesen Nutzer statt `E2E_USERNAME`/`E2E_PASSWORD`.
- [ ] **Step 3:** Specs durchsehen: alle Annahmen „leere DB“ (Zählungen, erste Einträge, feste IDs) auf selbst angelegte Daten umstellen; Liste der geänderten Specs im MR beschreiben. Lokal: Suite gegen eine laufende Preview grün (zweimal hintereinander, um Retry-Festigkeit zu zeigen).
- [ ] **Step 4:** `test_e2e`: Image `mcr.microsoft.com/playwright:v1.61.1-noble`, Tag `karibik`, `needs: [image_backend, image_portal]`, `resource_group: preview-${CI_MERGE_REQUEST_IID:-main}`, Script: `corepack enable && cd portal && pnpm install --frozen-lockfile`, `scripts/ci/wait-for-preview.sh`, `pnpm exec playwright test` mit `E2E_BASE_URL`, `E2E_CRM_BASE_URL`, `E2E_PROCESSES=1`, danach **erneut** Commit-Prüfung (Review Focus 1); Artefakte `tests/e2e/playwright-report`, `tests/e2e/e2e-results`, JUnit. Bei Merged-Results-Pipelines `CI_MERGE_REQUEST_SOURCE_BRANCH_SHA` statt `CI_COMMIT_SHA` verwenden. Das alte compose-`after_script` entfällt; `docker/e2e/` bleibt für lokal.
- [ ] **Step 5:** Mail-Prüfungen der Specs (`greenmail.ts`, `E2E_MAIL_API`): GreenMail-API ist nicht öffentlich → entweder Ingress-Pfad `/greenmail-api` nur für Runner-IPs/Basic-Auth oder die Specs prüfen Mails nicht mehr. Ohne Rückfrage nicht öffentlich machen → **Gate:** Rückfrage an den Nutzer, falls Specs Mails prüfen.
- [ ] **Step 6: Verifizieren** – Test-MR: `test_e2e` grün; zweiter Push während des Laufs → Lauf scheitert mit „Commit geändert“ statt grün.
- [ ] **Step 7: Checkpoint** – Nutzer prüft und committet.

## Phase 4 – Doku

### Task 12: Dokumentation

**Files:**
- Modify (flux-multicluster): `docs/clusters/gitops-karibik.md` (neuer Abschnitt **4j** mit Entscheidungen, Aufbau, Schritten, Stolpersteinen; Zeile in der Phasentabelle), `CLAUDE.md` (Zeile „Previews“), Spec-Status auf „umgesetzt“
- Modify (beg-verwaltung): `deployment/preview/README.md` (Ablauf für Entwickler: Preview-URL, Opt-out-Label, Testnutzer, Demo-Daten anheben)

- [ ] **Step 1:** Doku schreiben, Ist-Stand aus den Checkpoints (Spike-Ergebnisse, gewähltes Build-Werkzeug, tatsächliche Token-Namen) übernehmen.
- [ ] **Step 2: Checkpoint** – Nutzer prüft und committet.
