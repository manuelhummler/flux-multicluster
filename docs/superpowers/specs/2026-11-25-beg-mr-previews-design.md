# Design: Preview-Umgebungen pro Merge Request für beg-verwaltung (GitOps)

Stand: 2026-11-25 · Status: Entwurf, zur Freigabe (Rückfragen aus dem Builder-Check beantwortet, §9)

## 1. Absicht und Warum

Für jeden offenen Merge Request in `einfach-genossenschaft/beg-verwaltung` (gitlab.com) läuft im Cluster
**karibik** eine vollständige, öffentlich erreichbare Instanz der Anwendung (CRM-Backend, Portal, Datenbank,
Mail-Sink). Sie wird **per GitOps** (Flux Operator) angelegt, bei jedem Push aktualisiert und **erst gelöscht,
wenn der MR gemerged oder geschlossen ist**.

Zwei Zwecke:

1. **E2E-Tests:** Der CI-Job `test_e2e` startet keinen docker-compose-Stack mehr, sondern wartet, bis die
   Preview den aktuellen Commit ausliefert, und führt die Playwright-Suite (`tests/e2e`) gegen deren
   öffentliche URL aus. Grün = Job grün.
2. **Manuelles Testen:** Entwickler und Fachseite können den Stand eines MR direkt im Browser ausprobieren,
   ohne ihn lokal zu starten.

Hintergrund: Die bisherige Pipeline setzt einen Docker-Host-Runner mit `docker:dind`, gemeinsamem
`/root/.gradle` und `resource_group: heavy-tests` voraus. Der neue Kubernetes-Runner auf karibik
(`docs/clusters/gitops-karibik.md` 4h) erlaubt **keine privilegierten Pods** – bewusst, aus
Sicherheitsgründen. Ein DinD-Runner ist ausgeschlossen (Entscheidung des Nutzers).

## 2. Ziele, Nicht-Ziele, Anti-Ziele

**Ziele**
- Eine Preview pro offenem MR, URL aus der MR-Nummer ableitbar: `https://beg-mr-<iid>.hummler.com`
  (Portal) und dieselbe Domain unter `/api` bzw. dem CRM-Pfad für das Backend (genaue Pfadaufteilung: §6.3).
- Jeder neue Commit im MR → Preview mit **frischer Datenbank und frischem Dateispeicher**, befüllt aus den
  **Demo-Daten** (vollständig ausgedachte Daten).
- Eigene Keycloak-Clients **pro Preview**, als Manifeste im Repo, beim Abbau wieder entfernt.
- Images werden **rootless** in der CI gebaut (kein Docker-Daemon).
- Die CI braucht **keinen** Zugriff auf den Cluster und **keinen** Schreibzugriff auf flux-multicluster.

**Nicht-Ziele (vorerst)**
- documents4j (DOCX→PDF über Windows/Word) – in der Preview abgeschaltet.
- Elasticsearch/Volltextsuche – abgeschaltet (wie heute im E2E-Stack: `JMIX_SEARCH_SERVER_URL` auf toten Port,
  `MANAGEMENT_HEALTH_ELASTICSEARCH_ENABLED=false`).
- Echter Mailversand – Mails landen in GreenMail.
- Previews für Tags (das bleibt der bestehende Release-Weg). **`main` bekommt dagegen eine dauerhafte Preview**
  (§6.1), gegen die `test_e2e` nach dem Merge als Sicherheitsnetz läuft.
- Previews für MRs aus **Forks**.
- Lokaler Entwickler-Workflow: `docker/e2e/docker-compose*.yml` bleibt unverändert nutzbar.

**Anti-Ziele (das wäre ein Fehlschlag, auch wenn es technisch läuft)**
- Privilegierte Pods, DinD oder Docker-Socket irgendwo in der CI.
- MR-Branches können über ihre Manifeste Ressourcen außerhalb ihrer Preview verändern – insbesondere Clients in
  anderen Keycloak-Instanzen (`keycloak`, `pro-makler`) oder anderen Realms.
- Echte Personendaten in einer öffentlich erreichbaren Preview.
- Previews, die nach dem Merge liegen bleiben.
- E2E grün gegen einen **alten** Commit (Race zwischen CI und Flux).

## 3. Rahmenbedingungen (Ist-Stand, geprüft 2026-11-25)

- **Cluster karibik:** Kubernetes 1.36.2, Flux Operator **v0.54.1** (CRDs `ResourceSet`,
  `ResourceSetInputProvider` vorhanden), Cilium (default-deny-Muster), Traefik, cert-manager,
  CloudNative-PG, Longhorn. DNS `*.hummler.com` → karibik-LB. ValidatingAdmissionPolicy (v1) verfügbar.
- **Nodes:** barbossa 16 CPU/64 GB (~51 GB frei), gibbs 4 CPU/8 GB. CI-Jobs laufen per `node_selector` nur auf
  barbossa (4h).
- **Keycloak Operator 26.8.2** im Namespace `keycloak` (beobachtet nur diesen Namespace), Instanzen `keycloak`
  (hopps, später produktiv) und `pro-makler`. CRD `KeycloakOIDCClient` (`k8s.keycloak.org/v2alpha1`) vorhanden:
  - `metadata.name` = `clientId`, unveränderlich; `spec.keycloakCRName` + `spec.realm` unveränderlich.
  - Braucht auf der Keycloak-CR `spec.features.enabled: [client-admin-api:v2]` (**Preview-Feature**).
  - Operator meldet sich per Client-Credentials am `master`-Realm an: Secret `<keycloak-cr>-admin`
    (`client-id`, `client-secret`).
  - `client.auth.secretRef` → Secret mit Label `operator.keycloak.org/kind: KeycloakOIDCClient`.
  - `loginFlows` u.a. `STANDARD`, `SERVICE_ACCOUNT`; `serviceAccountRoles` nur mit `SERVICE_ACCOUNT`.
  - Löschen der CR löscht den Client in Keycloak (nur solange die Keycloak-CR existiert).
  - **Unbestätigt:** öffentliche Clients (ohne Secret, PKCE), Format von `serviceAccountRoles`
    (z.B. `realm-management/manage-users`), ob der Realm vorher existieren muss.
- **Flux Operator (Doku fluxoperator.dev):** `ResourceSetInputProvider` `type: GitLabMergeRequest` exportiert pro
  MR `id` (MR-Nummer), `sha`, `branch`, `author`, `title`; Filter per `labels`, `includeBranch`,
  `excludeBranch`, `limit`; Token-Secret mit `username`/`password`. `ResourceSet` mit `<< >>`-Templates,
  slim-sprig-Funktionen + `slugify`, `steps`, `wait`, `serviceAccountName` (Impersonation),
  Annotation `fluxcd.controlplane.io/copyFrom: "ns/name"` für Secrets/ConfigMaps, Garbage Collection beim
  Wegfall eines Inputs (erst Flux-Objekte, dann der Rest). Offizielles Beispiel „Ephemeral Environments for
  GitLab Merge Requests“.
- **Anwendung beg-verwaltung:** Jmix 3 / Spring Boot 4 / Java 25, Gradle 9.5.1; Backend-Port 8082
  (`/api/actuator/health`), Portal = nginx-SPA (Port 80, `API_BASE_URL`, `KEYCLOAK_URL`, `KEYCLOAK_REALM`,
  `KEYCLOAK_CLIENT_ID` als Laufzeit-Env → `/config.js`). Konfiguration des Backends komplett per Env
  (Vorlage: `docker/e2e/docker-compose.e2e.yml` und die Demo-`.env`). Das Backend legt selbst Nutzer in
  Keycloak an (`KEYCLOAKSERVICE_CONFIG_*`). Dateispeicher: Jmix-Default unter `/app/.jmix`
  (`work/filestorage/…`).
- **Demo-Daten** (Demo-Server `46.229.21.51`, `/home/apps/beg-deployment-new/demo/crm/`):
  versionierte Paare in `psql_dump/backups/<YYYYMMDD_HHMMSS>/{beg.sql,jmix.zip}`; aktuell 20261111_104103.
  `beg.sql` = Plain-SQL-`pg_dump` aus **Postgres 17.7**, ~18 MB, Owner `beg`, beginnt mit `\restrict`
  (braucht psql ≥ 17.6). `jmix.zip` ~111 MB, entpackt nach `/app` → `.jmix/work/filestorage/…`,
  `.jmix/conf/app-info`.
- **Bestehende Bausteine im flux-multicluster-Repo:** Muster `pro-makler` (GitRepository auf GitLab-Repo per
  SSH-Deploy-Key, Kustomizations auf `deployment/…`), Keycloak-Instanz `pro-makler` mit Realm-Import,
  GitLab Runner (4h) mit Reposilite-Gradle-Proxy (4i), SOPS/age.

## 4. Betrachtete Ansätze

| Ansatz | Kurz | Bewertung |
| --- | --- | --- |
| **A – Flux Operator ResourceSet + GitLab-MR-InputProvider** | Operator pollt offene MRs, rendert pro MR die Ressourcen, räumt bei Merge/Close per GC ab | **Gewählt.** GitOps-nativ, Lebenszyklus exakt wie gewünscht, CI ohne Cluster-/GitOps-Schreibrechte, Abbau auch ohne Pipeline. Preis: Polling-Verzögerung (per Webhook-Receiver auf Sekunden reduzierbar), E2E muss auf den richtigen Commit warten. |
| B – CI schreibt Preview-Ordner ins GitOps-Repo | Pipeline committet pro MR nach flux-multicluster, Cleanup-Job beim Merge | Verworfen: beg-CI bräuchte Schreibrechte auf das Infrastruktur-Repo, verwaiste Previews bei MRs ohne Pipeline, Commit-Rauschen. |
| C – CI deployt direkt (helm/kubectl) | Pipeline rollt selbst aus | Verworfen: kein GitOps, CI bräuchte Cluster-Admin. |

Weitere Entscheidungen im Gespräch:
- **Images rootless** bauen; **kein DinD-Runner** (Sicherheit).
- **Eigene Keycloak-Instanz `beg`** für die Previews (nicht die hopps-Instanz `keycloak`).
- **Eigene Clients pro Preview**, die Client-Manifeste liegen **im Repo** (bei den App-Manifesten der Preview).
- **Datenbank bei jedem Commit frisch** aus den Demo-Daten (Variante A im Gespräch), Dokumente ebenso.
- documents4j und Elasticsearch vorerst weglassen.
- Keycloak-Zugriff des Backends über das Client-Secret des per Operator angelegten Clients; dieser Client darf
  **Nutzer anlegen**.

## 5. Architektur

```
gitlab.com: beg-verwaltung MR !<iid> (Branch, Head-SHA)
     │  (1) CI: image_backend / image_portal rootless → registry.gitlab.com/.../ci/{backend,portal}:<sha>
     │  (2) CI: test_e2e wartet auf Preview mit <sha>, dann Playwright → https://beg-mr-<iid>.hummler.com
     ▼
karibik / Flux Operator
  ResourceSetInputProvider beg-merge-requests  (type GitLabMergeRequest, Token read-only)
        │ inputs: id, sha, branch, ...
        ▼
  ResourceSet beg-previews  (serviceAccountName: beg-previews, eingeschränkte Rechte)
     pro MR:
     ├─ Namespace beg-mr-<iid>
     ├─ GitRepository beg-mr-<iid>          → beg-verwaltung @ ref.commit <sha>, nur deployment/preview/
     ├─ Secrets (copyFrom): Registry-Pull, Client-Secret
     ├─ Kustomization beg-mr-<iid>-app      → deployment/preview/app      (Ziel-NS beg-mr-<iid>)
     │     postBuild.substitute: PREVIEW_ID, PREVIEW_SHA, PREVIEW_HOST, ...
     └─ Kustomization beg-mr-<iid>-keycloak → deployment/preview/keycloak (Ziel-NS keycloak)
           nur KeycloakOIDCClient, abgesichert per ValidatingAdmissionPolicy (§6.5)

  Plattform (flux-multicluster, dauerhaft):
     Keycloak-CR beg (+ Realm-Import beg), CNPG-DB keycloak-beg, Ingress auth-beg.hummler.com,
     Demo-Daten-Image, Policies, RBAC, Token-Secrets
```

### 5.1 Lebenszyklus

| Ereignis | Was passiert |
| --- | --- |
| MR geöffnet (bzw. Filter erfüllt) | InputProvider liefert neuen Input → ResourceSet legt Namespace, GitRepository, Kustomizations an. Pods ziehen `ci/backend:<sha>`; existiert das Image noch nicht, bleiben sie in `ImagePullBackOff`, bis die CI es gepusht hat. |
| Neuer Commit im MR | `sha` ändert sich → GitRepository zeigt auf neuen Commit, Substitution `PREVIEW_SHA` ändert sich → Datenbank und Dateispeicher werden **neu angelegt** (§6.2), Backend/Portal rollen mit dem neuen Image. |
| MR gemerged / geschlossen | Input entfällt → GC: erst Kustomizations (prunen ihre Objekte inkl. `KeycloakOIDCClient` → Operator löscht Client in Keycloak), dann Namespace usw. |

## 6. Bausteine im Detail

### 6.1 Plattform im flux-multicluster-Repo

- **InputProvider** `beg-merge-requests` (Namespace `beg-previews` o.ä.): `type: GitLabMergeRequest`,
  `url: https://gitlab.com/einfach-genossenschaft/beg-verwaltung`, Token-Secret (SOPS, basic-auth
  `username`/`password` = PAT bzw. Project Access Token mit Leserechten auf MRs/Branches).
  `filter.limit`: Obergrenze gleichzeitiger Previews (Vorschlag **8**, RAM-Budget §6.6).
  Auswahl der MRs: **alle offenen MRs aus Branches des Projekts selbst**, Opt-out per Label (`skip.labels` z.B.
  `preview::off`). Im Projekt arbeiten nur vertrauenswürdige Entwickler (Nutzer-Entscheidung). MRs aus Forks
  bekommen **keine** Preview (Filter bzw. Prüfung, dass der Branch im Upstream existiert); kämen künftig
  Außenstehende dazu, wird auf Opt-in per Maintainer-Label umgestellt.
- **Dauerhafte `main`-Preview:** zweiter InputProvider `type: GitLabBranch`, `includeBranch: "^main$"`, gleiche
  Vorlage, Host `beg-main.hummler.com`, ID `main`. Jeder Merge = neuer Commit = frische Demo-Daten. Dient als
  Ziel für `test_e2e` auf `main` und als stets aktuelle Demo des Hauptstands. Sie zählt mit ins RAM-Budget.
  Reaktionszeit: `reconcileEvery` 2–5 min plus optional Flux-`Receiver` `type: gitlab` (Webhook) für Sekunden.
- **ResourceSet** `beg-previews` mit eigenem ServiceAccount (Impersonation), Rechte: Namespaces `beg-mr-*`
  anlegen/löschen, Flux-Objekte in seinem Namespace, `copyFrom`-Lesen der Quell-Secrets. Nutzt `steps`, damit
  Secrets vor den Kustomizations stehen.
- **Pro-MR-Kustomizations** laufen mit eigenem, **eingeschränktem** ServiceAccount:
  - `-app`: darf nur im Namespace `beg-mr-<iid>` (namespaced Ressourcen, keine RBAC-/Cluster-Objekte).
  - `-keycloak`: darf nur `keycloakoidcclients` (und deren Client-Secrets, falls im Repo) im Namespace `keycloak`.
- **Keycloak-Instanz `beg`** (Namespace `keycloak`, Muster `pro-makler`): eigene DB im CNPG-Cluster,
  Ingress `auth-beg.hummler.com`, `features.enabled: [client-admin-api:v2]`, Admin-Client-Secret `beg-admin`
  für den Operator. **Realm `beg`** einmalig per `KeycloakRealmImport` (Basis: `docker/e2e/keycloak/realm-beg.json`
  aus beg-verwaltung), mit **festen Testnutzern passend zur Demo-Datenbank** (mindestens ein Sachbearbeiter-
  Account fürs CRM und einige Demo-Mitglieder fürs Portal, gleiche Benutzernamen/E-Mails wie die
  `beg_User`-/Mitglieder-Einträge im Dump; Passwörter nicht im Git, sondern per SOPS-Secret bzw. Realm-Import mit
  Platzhaltern). Dazu ein statischer, vertraulicher Client **`beg-e2e-admin`** (Service-Account mit
  Nutzerverwaltung im Realm `beg`), mit dem die E2E-Suite pro Pipeline ihren eigenen Testnutzer anlegt und wieder
  löscht (§6.7); sein Secret liegt als maskierte CI-Variable in beg-verwaltung.
- **Aufräumen von Preview-Nutzern:** ohne Preview-Code in der App (Nutzer-Entscheidung 2026-11-25). Der Realm
  zeichnet Admin-Events auf; wer einen Nutzer angelegt hat, steht dort als Client `beg-<id>-crm`. Ein CronJob der
  Plattform löscht stündlich Nutzer, deren anlegender Preview-Client keinen Namespace `beg-<id>` mehr hat, E2E-Nutzer
  älter als 24 h und verwaiste Preview-Clients. Feste Testnutzer (Realm-Import, kein Admin-Event) bleiben.
- **Demo-Daten-Image** `registry.gitlab.com/einfach-genossenschaft/beg-verwaltung/demo-data:<YYYYMMDD_HHMMSS>`
  (enthält `beg.sql` + `jmix.zip`). **Neue Stände lädt der Nutzer von Hand hoch** (Nutzer-Entscheidung; später
  evtl. automatisiert vom Demo-Server aus): Ein kleines `Dockerfile` (`FROM busybox`, `COPY beg.sql jmix.zip /data/`)
  und ein Befehl in `deployment/demo-data/README.md`, ausführbar auf jedem Rechner mit Docker und den beiden
  Dateien aus `psql_dump/backups/<stand>/`. Welcher Stand gilt, steht als Variable im Preview-Manifest
  (`DEMO_DATA_VERSION`; ein MR kann so neuere Demo-Daten testen).
- **Registry-Pull-Secret** (Deploy Token `read_registry`) als Quelle für `copyFrom`.
- **Netzwerk:** Cilium-Policies je Preview (aus dem Repo, §6.3) plus Plattform-Regeln: Traefik → Portal/Backend,
  Backend → Keycloak `beg`, Runner-Jobs dürfen die Preview über die öffentliche URL erreichen (geht ohnehin
  über `world`).

### 6.2 Daten pro Commit

- Postgres pro Preview als **CNPG-`Cluster` mit 1 Instanz** im Namespace der Preview, Name enthält den
  Kurz-SHA (`db-<sha7>`) → neuer Commit = neuer Cluster, alter wird gepruned. Alternative, falls zu schwer:
  einfacher `postgres:17`-Pod mit `emptyDir` (Daten sterben mit dem Pod).
- **Restore:** Init-Job/Init-Container (`postgres:17` ≥ 17.6 wegen `\restrict`) spielt `beg.sql` aus dem
  Demo-Daten-Image mit `ON_ERROR_STOP=on` ein, **bevor** das Backend startet. Danach migriert Liquibase beim
  Backend-Start auf den Schema-Stand des MR.
- **Dateispeicher:** Volume auf `/app/.jmix` (emptyDir oder kleines PVC), Init-Container entpackt `jmix.zip`
  aus dem Demo-Daten-Image. Wird pro Commit neu angelegt.

### 6.3 App-Manifeste im beg-verwaltung-Repo (`deployment/preview/`)

- `app/`: Deployments `backend`, `portal`, `mail` (GreenMail), DB (§6.2), Services, Ingress
  (`beg-mr-${PREVIEW_ID}.hummler.com`, TLS über cert-manager/`letsencrypt-prod`), CiliumNetworkPolicies,
  Init-Jobs/-Container für Demo-Daten. Werte per Flux-`postBuild.substitute` (`PREVIEW_ID`, `PREVIEW_SHA`,
  `PREVIEW_HOST`, `DEMO_DATA_VERSION`, …). Env des Backends wie in `docker-compose.e2e.yml`, angepasst:
  Issuer `https://auth-beg.hummler.com/realms/beg`, Client-ID `beg-mr-${PREVIEW_ID}-crm` + Secret,
  `KEYCLOAKSERVICE_CONFIG_*` auf denselben Client (Service-Account mit Nutzerverwaltung), CORS/Portal-URL auf
  die Preview-Host, ALTCHA-Testschlüssel wie im E2E-Stack.
- **Commit-Kennung für E2E:** Backend bekommt `INFO_PREVIEW_COMMIT=${PREVIEW_SHA}` (Spring Boot
  `management.info.env.enabled=true`), sichtbar unter `/api/actuator/info`. Der E2E-Job wartet, bis dort sein
  `CI_COMMIT_SHA` steht **und** Health `UP` ist.
- Pfadaufteilung Portal/Backend auf einem Host (z.B. `/` → Portal, `/api` und die Vaadin-CRM-Pfade → Backend)
  oder zwei Hosts (`beg-mr-<iid>` und `beg-mr-<iid>-crm`) – dem Builder überlassen, solange Keycloak-Redirects,
  CORS und der Secure Context passen.
- `keycloak/`: zwei `KeycloakOIDCClient`:
  - `beg-mr-${PREVIEW_ID}-crm`: vertraulich, `STANDARD` + `SERVICE_ACCOUNT`, Redirect auf die Preview,
    `serviceAccountRoles` für Nutzerverwaltung im Realm `beg`, `auth.secretRef` auf das Client-Secret.
  - `beg-mr-${PREVIEW_ID}-portal`: Portal-SPA (öffentlich/PKCE) – **vorab zu prüfen** (§7).

### 6.4 Client-Secrets

- **Pro Preview ein eigenes Secret** (gewünscht: eigene Clients pro Umgebung). Es darf **nicht** im Git stehen.
- Vorschlag: Die Plattform erzeugt es einmalig pro Preview und hält es stabil, solange der MR offen ist:
  ResourceSet legt ein leeres, gelabeltes Secret in `keycloak` an; ein kleiner Generator (Job im ersten Step
  oder ein bestehender Secret-Generator-Controller) füllt `data` nur, wenn leer; per `copyFrom` landet es im
  Preview-Namespace. GC löscht beide mit der Preview.
- Fallback, falls das zu fragil ist: ein gemeinsames Preview-Client-Secret (SOPS) für alle Preview-Clients,
  per `copyFrom` verteilt. Die Clients selbst bleiben pro Preview getrennt.

### 6.5 Absicherung gegen MR-Manifeste

Die Manifeste kommen aus beliebigen MR-Branches. Deshalb:
- Kustomization `-keycloak` mit ServiceAccount, der **nur** `keycloakoidcclients` in `keycloak` verwalten darf.
- **ValidatingAdmissionPolicy** auf `KeycloakOIDCClient`, die für Objekte dieses ServiceAccounts erzwingt:
  `spec.keycloakCRName == 'beg'`, `spec.realm == 'beg'`, `metadata.name` beginnt mit `beg-mr-`,
  `serviceAccountRoles` nur aus einer Allowlist (Nutzerverwaltung im Realm `beg`), Redirect-URIs nur auf
  `https://beg-mr-*.hummler.com/…`.
- Kustomization `-app` mit ServiceAccount nur im eigenen Namespace; keine Cluster-Ressourcen, keine RBAC.
- Preview-Namespaces mit PSS `restricted` (bzw. `baseline`, falls ein Image es erfordert).

### 6.6 Ressourcen

Grobe Schätzung pro Preview: Backend 2–2,5 GB, Postgres 0,3–0,5 GB, Portal 50 MB, GreenMail 0,3 GB →
**~3–3,5 GB**. Keycloak `beg` dauerhaft ~1 GB. Bei 8 Previews ~28 GB auf barbossa – passt neben den Apps
(~51 GB frei), CI-Jobs brauchen zusätzlich Platz. PriorityClass für Previews niedriger als Apps, höher oder gleich
CI (`ci-low`) – Builder entscheidet.

### 6.7 CI-Umbau in beg-verwaltung (`.gitlab-ci.yml`)

- `test_backend`: bereits umgestellt (Tag `karibik`, kein `/root/.gradle`, eigene Limits) – uncommitted.
- **Image-Builds rootless** (`image_backend`, `image_portal`, `build_backend_*`, `build_frontend_*`):
  BuildKit rootless oder Buildah (Dockerfile nutzt `RUN --mount=type=cache` → Kaniko scheidet aus).
  MR-Images zusätzlich mit Tag `<CI_COMMIT_SHA>` (Preview zieht per SHA); die Wiederverwendung per
  Tree-Hash (`git rev-parse HEAD:source`) bleibt, indem ein vorhandenes Image registry-seitig umgetaggt wird
  (`crane`/`skopeo`/`regctl`). `docker manifest inspect` → `crane manifest` o.ä.
  Jmix-Zugangsdaten im Image-Build: `premiumRepoPass` aus `/gradle-secrets` (Runner, 4i) als Build-Secret bzw.
  in `source/gradle.properties` der Build-Stage – nicht im finalen Image.
- **`test_e2e` neu:** Image `mcr.microsoft.com/playwright:v1.61.1-noble`, `pnpm install` im Workspace,
  Warten auf Preview (Commit + Health, Timeout ~20 min), dann `playwright test` mit
  `E2E_BASE_URL=https://beg-mr-$CI_MERGE_REQUEST_IID.hummler.com` usw., Testnutzer aus dem Realm `beg`
  (CI-Variablen). Kein compose, kein `docker cp`; Reports direkt aus dem Checkout. Die bisherigen
  `localhost`-Proxies für den Secure Context entfallen (echte HTTPS-Origin).
- `resource_group: heavy-tests` entfällt.
- **Runner:** Gruppen-Runner auf karibik für `einfach-genossenschaft` (4h), Tag `karibik`.
- **Tests stellen ihren Anfangszustand selbst her** (Nutzer-Entscheidung): Die Suite läuft gegen die Preview mit
  Demo-Daten. Ein Playwright-Setup legt pro Pipeline einen **eigenen E2E-Nutzer** im Realm `beg` an
  (`e2e-<CI_PIPELINE_ID>-<zufall>@…`, über den Client `beg-e2e-admin`), Teardown löscht ihn. Damit gilt die
  heutige Annahme „E2E-Nutzer ist noch kein Mitglied“ für jeden Lauf neu, auch bei Retries auf demselben Commit.
  Specs, die heute von einer **leeren** Datenbank ausgehen (Zählungen, „erstes Projekt“ o.ä.), werden so
  umgeschrieben, dass sie nur Daten prüfen, die sie selbst angelegt haben. `E2E_USERNAME`/`E2E_PASSWORD` als feste
  CI-Variablen entfallen. Umfang der Umschreibung ermittelt der Builder (Durchsicht `tests/e2e`).
- Parallele Läufe gegen dieselbe Preview (Retry während ein Lauf noch läuft) per `resource_group: preview-$CI_MERGE_REQUEST_IID`
  serialisieren.
- **`main`:** `test_e2e` läuft nach dem Merge gegen `beg-main.hummler.com` und wartet dort auf den Merge-Commit.

## 7. Risiken und vorab zu prüfen (Spikes)

1. **Öffentlicher Portal-Client per `KeycloakOIDCClient`** (ohne Secret/PKCE) – nicht dokumentiert. Test auf
   der `beg`-Instanz. Fallback: Portal-Client vertraulich mit BFF, oder ein **statischer** Portal-Client im
   Realm-Import mit Redirect-Wildcard am Pfadende auf einem gemeinsamen Host (würde Pfad-Routing erzwingen) –
   Entscheidung dann mit dem Nutzer.
2. **`serviceAccountRoles`-Format** und ob damit `realm-management`-Client-Rollen (manage-users, view-users …)
   vergeben werden können.
3. **Rootless-Build unter PSS baseline ohne privileged** (BuildKit rootless vs. Buildah, AppArmor/Seccomp auf den
   Nodes). Falls keiner geht: Rückfrage an den Nutzer (z.B. eigener Build-Runner mit gelockerten, aber nicht
   privilegierten Einstellungen).
4. **Preview-Feature `client-admin-api:v2`**: Verhalten bei Operator-Updates; Renovate-Bumps des Operators nur
   mit Test.
5. **Commit-Race:** Pipelines vom Typ „merged results“ haben eine andere `CI_COMMIT_SHA` als der MR-Head – prüfen,
   welcher Pipeline-Typ läuft, und ggf. `CI_MERGE_REQUEST_SOURCE_BRANCH_SHA` verwenden.
6. **Let’s-Encrypt-Limits:** pro Preview ein Zertifikat (50/Woche je registrierter Domain) – bei vielen Pushes
   unkritisch, weil der Host pro MR gleich bleibt.

## 8. Dem Builder überlassen (mit Vorschlag)

- Namespace für InputProvider/ResourceSet, Namen der ServiceAccounts, genaue RBAC-Regeln.
- Einzelner Host mit Pfad-Routing vs. zwei Hosts pro Preview.
- CNPG-Cluster pro Commit vs. einfacher Postgres-Pod.
- Opt-out-Label-Name und `filter.limit` (Vorschlag 8).
- Webhook-Receiver ja/nein (Vorschlag ja, wenn der Polling-Takt zu langsam ist).
- Generator-Mechanik für die Client-Secrets (§6.4).
- Rückmeldung der Preview-URL an den MR (Flux-Notification `gitlabmergerequestcomment` oder GitLab-Environment
  im CI-Job).
- Doku als neuer Abschnitt in `docs/clusters/gitops-karibik.md` (4j) und Kurz-README in `deployment/preview/`.
- Mechanik, mit der Fork-MRs ausgeschlossen werden.
- Genaue Liste der festen Testnutzer (aus dem Demo-Dump abgeleitet) und Ablage ihrer Passwörter.

## 9. Antworten aus dem Builder-Check (2026-11-25)

| # | Frage | Entscheidung |
| --- | --- | --- |
| 1 | E2E gegen Demo-Daten? Retries auf demselben Commit? | Tests stellen ihren Anfangszustand selbst her: eigener E2E-Nutzer pro Pipeline, Specs prüfen nur eigene Daten (§6.7). |
| 2 | Was ersetzt `test_e2e` auf `main`? | Dauerhafte `main`-Preview (`GitLabBranch`-Provider, §6.1). |
| 3 | Welcher Runner? | Gruppen-Runner karibik für **`einfach-genossenschaft`** (nicht `hummler-gbr`; die kommt später als eigener Runner dazu). Doku 4h korrigiert. |
| 4 | Wer öffnet MRs, Forks? | Nur vertrauenswürdige Entwickler → alle MRs mit Opt-out-Label; Forks ausgeschlossen. |
| 5 | Demo-Daten ins Image? | Manuell hochladen (Dockerfile + README), später evtl. automatisiert. |
| 6 | Accounts / angesammelte Keycloak-Nutzer? | Feste Testnutzer passend zur Demo-DB im Realm-Import; Preview-Nutzer mit Attribut `preview=<id>`, Aufräum-CronJob. |
