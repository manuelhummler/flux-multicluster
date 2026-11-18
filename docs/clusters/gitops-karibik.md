# GitOps-Setup & App-Migration (Karibik)

Schließt direkt an [node-setup-hetzner.md](node-setup-hetzner.md) §14 an: Der **flux-operator**
ist installiert, jetzt wird das Cluster per GitOps aus diesem Repo befüllt – und die Apps
werden **stück für stück** von `captain-kube` nach `karibik` umgezogen.

> Ziel: captain-kube wird am Ende leergeräumt und neu aufgesetzt (bzw. als 3. Node in karibik
> wiederverwendet). Siehe [README.md](README.md).

## 1. Repo-Anbindung (FluxInstance)

Statt des klassischen `flux bootstrap` zeigt eine `FluxInstance`-CR auf dieses Repo. Sie liegt
versioniert unter [clusters/karibik/flux.yaml](../../clusters/karibik/flux.yaml):

- Flux-Distribution `2.9.1`, Registry `ghcr.io/fluxcd`
- Sync-Quelle: `ssh://git@github.com/manuelhummler/flux-multicluster`, Branch `main`,
  Pfad `clusters/karibik`
- `pullSecret: flux-system` – der SSH-Deploy-Key muss als Secret `flux-system` im Namespace
  `flux-system` existieren (Read-Only-Deploy-Key des Repos)
- `networkPolicy: false` – NetworkPolicies kommen von **Cilium**, nicht von Flux

Flux liest ab jetzt [clusters/karibik/infrastructure.yaml](../../clusters/karibik/infrastructure.yaml):
dort steht eine Liste von `Kustomization`-Objekten, die über `dependsOn` eine
Deploy-Reihenfolge bilden.

## 2. Verzeichnis-Struktur (base / overlay)

Gleiches Muster wie captain-kube (siehe [CLAUDE.md](../../CLAUDE.md)):

```
infrastructure/
  base/apps/<app>/        # wiederverwendbare App-Definition (HelmRelease, Namespace …)
  base/helm-repository/   # HelmRepository-Quellen
  karibik/<app>/          # Cluster-Overlay: referenziert base + Patches/Secrets
clusters/karibik/
  flux.yaml               # FluxInstance (Repo-Anbindung)
  infrastructure.yaml     # Orchestrierung + Reihenfolge (dependsOn)
```

Die **base**-Definitionen existieren bereits für alle Apps. Eine App nach karibik zu holen
heißt deshalb meist nur: Overlay anlegen + Eintrag in `infrastructure.yaml` + ggf.
Cluster-Secrets.

## 3. Phasenmodell der Migration

| Phase | Inhalt | Status |
| ----- | ------ | ------ |
| **0** | Fundament: Secrets/SOPS, cert-manager, cluster-issuer, metrics-server, longhorn, Cilium-Adoption | **erledigt 2026-11-03** |
| 1 | Datenschicht: CloudNative-PG (statt Zalando), MariaDB Galera; **kein MinIO** (Backups → DigitalOcean) | **erledigt 2026-11-03** (Daten ziehen pro App in Phase 2/3 um) |
| 2 | Plattform/Auth: keycloak (**Operator statt Bitnami**, siehe 4c), oauth2-proxy, Monitoring-Stack (Grafana-OAuth) | **in Arbeit** – keycloak Test-Rollout erledigt 2026-11-08 (läuft parallel auf `auth.karibik.hummler.com`), oauth2-proxy/Monitoring offen |
| 3 | Apps: vaultwarden, kimai, dtrack, apicurio, minecraft, hopps-* (juno-care am 2026-11-08 ersatzlos entfernt, kein Umzug; fest-der-demokratie am 2026-11-18 ersatzlos entfernt – `fest-der-demokratie.de` zeigte schon auf einen fremden Server `23.88.102.169`, captain-kube bekam keinen Traffic mehr) | offen |
| neu | **Hermes Agent** (KI-Harness) unter `hermes.hummler.com`: keine Migration, Neuinstallation nur auf karibik (siehe 4e) | **läuft seit 2026-11-08**; offen: **Backup der PVC**, Egress erweitern, OIDC |
| neu | **Renovate** (self-hosted) als CronJobs für GitHub `flux-multicluster` und GitLab `hummler-gbr/pro-makler`, ersetzt die Mend-App (siehe 4f) | **vorbereitet 2026-11-11**, Rollout offen |
| 4 | captain-kube leerräumen & als 3. Node rejoinen; **danach** Control-Plane auf private IPs (siehe 4d) | offen |

---

## 4. Phase 0 – Fundament

### 4.1 SOPS: age-Key im Cluster hinterlegen (einmalig, out-of-band)

Fast jede Kustomization nutzt `decryption.provider: sops` und bezieht Werte per
`postBuild.substituteFrom` aus dem Secret `cluster-secrets`. Flux entschlüsselt mit einem
Secret `sops-age` im Namespace `flux-system`. Das wird **nicht** über Git eingespielt:

```bash
# karibik nutzt bewusst denselben age-Key wie captain-kube
kubectl --context karibik -n flux-system create secret generic sops-age \
  --from-file=age.agekey=/pfad/zu/deinem.agekey
```

Die Verschlüsselungsregeln stehen in [infrastructure/karibik/.sops.yaml](../../infrastructure/karibik/.sops.yaml)
(gleicher Recipient wie captain-kube – so gewollt).

### 4.2 cluster-secrets befüllen und verschlüsseln

```bash
cd infrastructure/karibik/configs
cp secret.env.example secret-encrypted.env      # Vorlage mit allen Keys
# Werte eintragen (siehe unten), dann verschlüsseln:
sops --encrypt --in-place secret-encrypted.env  # nutzt ../.sops.yaml
git add secret-encrypted.env                     # NUR verschlüsselt committen
```

Für Phase 0 zwingend benötigte Keys (Details:
[configs/README.md](../../infrastructure/karibik/configs/README.md) und
[configs/secret.env.example](../../infrastructure/karibik/configs/secret.env.example)):

| Key | Zweck | karibik-Wert |
| --- | ----- | ------------ |
| `DOMAIN` | Ingress-Hosts (`longhorn.${DOMAIN}`, `hubble.${DOMAIN}`) | z. B. `da-vinci.alanda.io` |
| `EMAIL` | Let's-Encrypt-ACME-Registrierung (cluster-issuer) | deine Admin-Mail |
| `API_SERVER_IP` | Ciliums kube-proxy-Replacement | `142.132.246.190` (LB) |
| `API_SERVER_PORT` | dito | `6443` |

> `*.agekey` und `*decrypted.*` sind in [.gitignore](../../.gitignore) – nie im Klartext committen.

### 4.3 Deploy-Reihenfolge (infrastructure.yaml)

Die Kustomizations in [clusters/karibik/infrastructure.yaml](../../clusters/karibik/infrastructure.yaml)
hängen so zusammen:

```
configs ─┐ (liefert cluster-secrets)
prometheus-operator-crds ─┬─ cert-manager ── cluster-issuer
                          ├─ metrics-server
                          ├─ longhorn
                          └─ traefik-crds ── traefik (dependsOn: cert-manager)
```

- **configs** zuerst – ohne `cluster-secrets` schlagen alle `substituteFrom`-Kustomizations fehl.
- **cert-manager** → **cluster-issuer** (`letsencrypt-prod`, braucht `${EMAIL}`).
- **longhorn** braucht `${DOMAIN}` (UI-Ingress). Das UI-Ingress nutzt die
  oauth2-proxy-Middleware (erst Phase 2); die Storage-Funktion selbst ist sofort voll da.
- **traefik** hat jetzt `dependsOn: cert-manager` (vorher auskommentiert), damit TLS-Issuing
  bereitsteht.

### 4.4 Overlay-Muster (Beispiel cert-manager)

Jedes Phase-0-Overlay referenziert nur die Base:

```yaml
# infrastructure/karibik/cert-manager/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base/apps/cert-manager
```

`metrics-server` bringt zusätzlich eine `namespace.yaml` mit (Pod-Security `restricted`),
analog captain-kube.

### 4.5 Cilium: bestehendes Helm-Release in Flux übernehmen (Adoption)

Verifizierter Ist-Stand (2026-10-31): Cilium wurde beim Node-Setup **out-of-band per Helm**
installiert (siehe [node-setup-hetzner.md](node-setup-hetzner.md) §10) und läuft gesund:

- Version `v1.19.5`, Namespace **`cilium`** (nicht `kube-system`), `kubeProxyReplacement` aktiv
- Helm-Release `cilium` (`managed-by: Helm`)

Statt es out-of-band zu lassen, wird es **von Flux adoptiert** – aber als **No-Op-Übernahme**:

- Eigenständiges Overlay [infrastructure/karibik/cilium/](../../infrastructure/karibik/cilium/)
  (referenziert **nicht** `base/apps/cilium`, da dessen Values auf captain-kube zugeschnitten
  sind und das laufende CNI umkonfigurieren würden).
- Das HelmRelease pinnt `releaseName`/`storageNamespace`/`targetNamespace` auf `cilium` und
  reproduziert die Values exakt aus `helm -n cilium get values cilium`. helm-controller setzt
  die bestehende Release-History per `upgrade` fort (Ownership-Annotationen passen bereits) –
  es entsteht **keine** zweite Installation.
- **Risiko:** Ein minimaler Chart-Render-Unterschied kann den `cilium`-DaemonSet neu ausrollen.
  Cilium rollt Agents graceful (Pods behalten Netzwerk via BPF-Maps), Rest-Risiko aber > 0.
  Daher Cilium **als eigenen Commit zuerst** ausrollen und beobachten:

```bash
flux --context karibik reconcile kustomization cilium --with-source
flux --context karibik get helmrelease cilium -n cilium
kubectl --context karibik -n cilium rollout status ds/cilium --timeout=2m
```

> Künftige Cilium-Änderungen laufen dann über das Overlay (Renovate hält die Chart-Version
> aktuell). Werte ändern = DaemonSet-Rollout, also mit Bedacht.

**Erledigt am 2026-11-03:** Die Kustomization war zunächst mit `suspend: true` eingecheckt und
wurde nach Abschluss von Phase 0 bewusst aktiviert. Ergebnis wie erhofft ein No-Op: Helm-Revision 2
„Upgrade complete", kein Pod wurde neu gestartet (DaemonSets cilium/cilium-envoy und
cilium-operator unverändert), `cilium status` OK. Cilium wird seitdem von Flux verwaltet.

**Voraussetzungen, damit Flux überhaupt reconcilen kann** (Stand 2026-11-03 **beide erledigt**):

- GitRepository `flux-system` braucht einen gültigen SSH-Deploy-Key (Secret `flux-system` mit
  `identity`/`identity.pub`/`known_hosts`) – sonst zieht Flux keine neuen Commits.
  Das Secret war vorhanden und korrekt (passt zum GitHub-Deploy-Key „karibik deploy key"), aber
  die **FluxInstance im Cluster** hatte `sync.pullSecret` noch auskommentiert – die GitRepository
  bekam daher keinen `secretRef` („invalid 'ssh' auth option: 'identity' is required").
  Henne-Ei: Die Korrektur stand in Git, Flux konnte sie ohne Auth nicht ziehen. Einmalig gelöst mit
  `kubectl apply -f clusters/karibik/flux.yaml` auf dem Node; seitdem folgt Flux wieder `main`.
- `sops-age`-Secret muss im Cluster liegen (Schritt 4.1), sonst bleibt `configs`/`cluster-secrets`
  leer und cert-manager/longhorn/traefik rot. Erledigt: Key 1:1 aus captain-kube übernommen
  (`kubectl -n flux-system get secret sops-age` dort → `create secret` hier, per SSH-Pipe).

**Stand nach dem Rollout (2026-11-03):** configs, cert-manager, cluster-issuer (`letsencrypt-prod`
Ready), metrics-server und longhorn sind grün. traefik scheiterte zunächst an der Values-
Schemaprüfung von Chart 41 (`logs` → `log`/`accessLog` umbenannt, siehe Fix in
`infrastructure/base/apps/traefik/helm-release.yaml`). Derselbe Fehler blockierte auf captain-kube
seit 2026-06-30 das Upgrade 40.3.0 → 41.0.2. Nach dem Fix sind beide Cluster auf Chart 41.0.2.
Cilium wurde anschließend adoptiert (4.5) – **Phase 0 ist damit abgeschlossen.**

**Domain-Entscheidung:** karibik nutzt während der Migration die eigene Domain
`karibik.hummler.com` (A-Records `karibik.hummler.com` und `*.karibik.hummler.com` → LB
`2.31.111.21`, „port-royal"). Damit laufen Apps parallel auf beiden Clustern und sind testbar;
der eigentliche Cutover pro App bleibt der DNS-Eintrag unter `kube.hummler.com` bzw. den
App-Domains. Longhorn-UI und Traefik-Dashboard antworten bis Phase 2 mit HTTP 500, weil ihre
Ingresses die oauth2-proxy-ForwardAuth-Middleware referenzieren, deren Service noch fehlt.

### 4.6 Ausrollen & prüfen

```bash
git push                                   # Flux reconciled automatisch (Intervall 10m)
flux --context karibik reconcile kustomization flux-system --with-source   # sofort anstoßen
flux --context karibik get kustomizations  # Status aller Kustomizations
kubectl --context karibik get helmrelease -A
```

---

## 4b. Phase 1 – Datenschicht

### Entscheidungen (2026-11-03)

| Thema | Entscheidung | Begründung |
| ----- | ------------ | ---------- |
| Postgres-Operator | **nur CloudNative-PG**, kein Zalando auf karibik | Zalando hält auf captain-kube nur noch keycloak, vaultwarden, dtrack (2,1 GB / 14 MB / 10 MB). Alles Neuere (apicurio, alle hopps-Umgebungen) läuft bereits auf CNPG inkl. Barman-Backups. Umzug per `pg_dump`/`pg_restore` (PG 16 → 17). |
| Objektspeicher | **kein MinIO auf karibik** | MinIO enthielt ausschließlich DB-Backups (16 GB Zalando-Dumps, 1,8 GB CNPG, 0,9 GB mariadb). Backups gehen in den vorhandenen DigitalOcean-Space `hopps` (fra1), der schon heute vom mariadb-operator und vom Zalando-Logical-Backup genutzt wird. Off-Site bei einem anderen Anbieter ist für Backups wertvoller als Nähe; preislich liegen DO Spaces (5 $/250 GiB) und Hetzner Object Storage (6,49 €/1 TB) bei unserem Volumen gleichauf. |
| MariaDB | **Galera mit 3 Replikaten von Anfang an**, ohne Anti-Affinity | siehe unten |

### MariaDB Galera auf 2 Nodes („Variante A")

Der mariadb-operator (Admission-Webhook) erlaubt Galera **nur mit `replicas` > 1 und ungerade**
(gerade nur mit `replicasAllowEvenNumber`). Eine Einzelinstanz mit Galera ist nicht möglich, und
ein späteres Einschalten von Galera auf einer Einzelinstanz ist nicht als Migrationspfad dokumentiert.

Deshalb startet karibik direkt mit **3 Galera-Pods auf 2 Nodes** (Overlay
[infrastructure/karibik/mariadb/](../../infrastructure/karibik/mariadb/)):

- `affinity.antiAffinityEnabled: false` – die Pods verteilen sich 2+1 auf die Nodes. Mit Anti-Affinity
  bliebe der 3. Pod Pending und der Cluster würde nicht bootstrappen.
- Fällt der Node mit **1** Pod aus → Quorum 2/3 bleibt, Betrieb läuft weiter.
- Fällt der Node mit **2** Pods aus → kein Quorum; die Operator-Recovery (`galera.recovery`,
  `minClusterSize: 1`, aus der Base) bootstrappt den Cluster vom verbliebenen Pod neu. Kurzer Ausfall,
  kein manueller Eingriff.
- MaxScale mit **2** Replikaten (reicht laut Operator-Doku für unterbrechungsfreie Upgrades).
- Backup: nur `backup-digital-ocean`, Prefix **`karibik-mariadb`** (captain-kube nutzt
  `hopps-cloud-mariadb` – getrennte Prefixe, sonst löscht `maxRetention` fremde Backups).
  Das MinIO-Backup der Base wird per `$patch: delete` entfernt.
- Die kimai-Objekte (`Database`/`User`/`Grant` im Namespace `kimai`) sind ebenfalls per Delete-Patch
  ausgeblendet, bis kimai in Phase 3 umzieht – dann die drei Patches entfernen.
- Secret `mariadb-secret` enthält nur die DigitalOcean-Keys (übernommen aus captain-kube, neu
  verschlüsselt für `infrastructure/karibik/.sops.yaml`).

**Beim 3. Node (Phase 4):**

1. In `patch-mariadb.yaml` `antiAffinityEnabled: true` setzen (optional auch für MaxScale und auf 3
   Replikate erhöhen).
2. Pushen; der Operator rollt die Pods nacheinander neu, sie verteilen sich auf drei Nodes.
3. Prüfen: `kubectl -n mariadb get pods -o wide` (ein Pod pro Node), `kubectl get mariadb -n mariadb`.

Keine Änderung an `replicas` oder an Daten nötig.

### CloudNative-PG auf karibik (erledigt 2026-11-03)

Overlays [infrastructure/karibik/cloudnative-pg/](../../infrastructure/karibik/cloudnative-pg/) (Operator,
Chart 0.23.2 / CNPG 1.25.1) und
[infrastructure/karibik/cloudnative-pg-cluster/](../../infrastructure/karibik/cloudnative-pg-cluster/):

- **2 Instanzen** (Primary + Streaming-Replica, je eine pro Node, PG 17.4). Failover macht der Operator,
  ein Quorum braucht CNPG nicht. Beim 3. Node optional `instances: 3`.
- StorageClass `longhorn-postgres-replica-storage`: **ein** Longhorn-Replikat, `strict-local` – CNPG
  repliziert selbst, doppelte Replikation wäre nur Latenz. Fällt ein Node aus, baut CNPG die Instanz
  dort aus dem Primary neu auf.
- Rollen + Datenbanken: `apicurio-studio`, `apicurio-registry`, `keycloak`, `dtrack`, `vaultwarden`
  (`managed.roles` + `Database`-CRs). Passwörter liegen als `kubernetes.io/basic-auth`-Secrets
  `<rolle>-secret` im Namespace `cnpg-cluster` (SOPS unter `cloudnative-pg-cluster/secrets/`).
  **Dieselben Werte wie auf captain-kube**: apicurio aus den bestehenden SOPS-Dateien, keycloak/dtrack/
  vaultwarden aus den Zalando-Credential-Secrets übernommen – die Apps behalten beim Umzug ihre
  Zugangsdaten, nur der Host ändert sich auf `postgres-cluster-rw.cnpg-cluster`.
- Backup: Barman in den DigitalOcean Space `hopps`, **Prefix `karibik-cnpg`**, alle 12 h, 30 Tage
  Retention, WAL-Archiv kontinuierlich. Erstes Backup und `ContinuousArchiving=True` am 2026-11-03
  verifiziert.
- Cilium: Policies wie captain-kube, plus Replikation zwischen den Instanzen und Egress zu
  `*.digitaloceanspaces.com:443` (toFQDNs; der DNS-Proxy kommt aus der default-deny-Regel der Base).

**Stolperstein Webhook (karibik-spezifisch):** Die Cilium-Entity `kube-apiserver` enthält nur die IPs
der `kubernetes`-Endpoints, auf karibik die **öffentlichen** Node-IPs. Der Webhook-Aufruf des apiservers
vom jeweils anderen Node läuft über den VXLAN-Tunnel und trägt die Identität `remote-node` → Policy
denied, Flux meldete `failed calling webhook ... context deadline exceeded`. Auf captain-kube (ein Node,
Endpoint-IP = Node-IP) tritt das nicht auf. Fix: `patch-webhook-policy.yaml` lässt zusätzlich `host` und
`remote-node` auf 9443 zu. Dasselbe Muster gilt für jeden Operator-Webhook hinter default-deny.
Diagnose: `hubble observe --to-namespace <ns> --port <webhook-port>` auf dem Cilium-Pod des Ziel-Nodes.

**Stolperstein kubectl:** `kubectl get database` / `get backup` trifft auf karibik die
**mariadb-operator**-Typen (gleicher Kind-Name). Für CNPG immer voll qualifizieren:
`kubectl get databases.postgresql.cnpg.io`, `kubectl get backups.postgresql.cnpg.io`.

### Rezept: App von Zalando auf CNPG umziehen (Phase 2/3)

1. App-Overlay auf karibik: DB-Secret aus `cloudnative-pg-cluster/secrets/<rolle>-encrypted.env` per
   `secretGenerator` auch im App-Namespace erzeugen (Muster apicurio: `apicurio-registry-db`).
2. HelmRelease-Patch: Host `postgres-cluster-rw.cnpg-cluster`, Port 5432, Secret-Name/Keys
   (`username`/`password`) statt `*.credentials.postgresql.acid.zalan.do`. vaultwarden: `secret-uri`
   im App-Secret neu bauen (`postgresql://vaultwarden:<pw>@postgres-cluster-rw.cnpg-cluster/vaultwarden`).
3. Daten: auf captain-kube `pg_dump -Fc -U postgres <db>` im Zalando-Pod, nach karibik kopieren,
   `pg_restore -U postgres -d <db> --no-owner --role=<rolle>` im CNPG-Primary. App auf captain-kube
   vorher stoppen (Scale 0), damit kein Stand verloren geht.
4. App auf karibik starten, prüfen, DNS umbiegen.

## 4c. Phase 2 – Plattform/Auth

### Keycloak: Operator statt Bitnami-Chart (Entscheidung 2026-11-06)

captain-kube betreibt Keycloak 26.3.1 über das Bitnami-Chart (`base/apps/keycloak`). Das Image
`docker.io/bitnami/keycloak:26.3.1-debian-12-r1` existiert dort **nicht mehr** – Bitnami hat 2025 den
Katalog geleert, alte Tags liegen nur noch unter `bitnamilegacy/` (eingefroren, keine Updates). Auf
captain-kube läuft der Pod nur noch aus dem Node-Image-Cache; ein frischer Rollout der Base auf karibik
endet im `ImagePullBackOff`. Daher auf karibik der **offizielle Keycloak Operator** (Upstream-Image
`quay.io/keycloak/keycloak`), die Bitnami-Base bleibt für captain-kube bis zur Abschaltung unverändert.

Aufbau (drei Flux-Kustomizations in [clusters/karibik/infrastructure.yaml](../../clusters/karibik/infrastructure.yaml)):

| Kustomization | Quelle | Inhalt |
| --- | --- | --- |
| `keycloak-operator-source` | [base/apps/keycloak-operator](../../infrastructure/base/apps/keycloak-operator/) | `GitRepository` auf `keycloak/keycloak-k8s-resources` (Tag = Keycloak-Version, Renovate bumpt) + Namespace `keycloak` (Pod-Security `restricted`) |
| `keycloak-operator` | GitRepository `keycloak-operator`, Pfad `./kubernetes` | CRDs (`Keycloak`, `KeycloakRealmImport`, `KeycloakOIDCClient`, `KeycloakSAMLClient`) + Operator-Deployment; `patches` ergänzen den fehlenden `securityContext` |
| `keycloak` | [karibik/keycloak](../../infrastructure/karibik/keycloak/) | `Keycloak`-CR, Traefik-Ingress, Cilium-Policies, Secret `keycloak-db` |

Es gibt kein Upstream-Helm-Chart – das Muster entspricht `traefik-crds` (GitRepository + Kustomization
auf ein Unterverzeichnis). Operator und Keycloak-CR liegen im **selben** Namespace, weil der Operator
nur seinen eigenen Namespace beobachtet.

Wesentliche Entscheidungen in [keycloak.yaml](../../infrastructure/karibik/keycloak/keycloak.yaml):

- **DB**: `postgres-cluster-rw.cnpg-cluster`, Secret `keycloak-db` aus derselben SOPS-Datei wie die
  CNPG-Rolle (`cloudnative-pg-cluster/secrets/keycloak-encrypted.env`) – kein Zalando-Umbau nötig.
- **Hostname** `https://auth.${DOMAIN}` (= `auth.karibik.hummler.com`), `strict` (Default). Issuer für
  oauth2-proxy/Grafana während der Migration: `https://auth.karibik.hummler.com/realms/hopps`.
  Die extraHosts von captain-kube (`auth.hopps.cloud`, `auth.hopps.app`) kommen erst beim Cutover in
  den Ingress – vorher kann cert-manager dafür kein Zertifikat ausstellen (DNS zeigt auf captain-kube).
- **TLS bei Traefik**: `http.httpEnabled: true`, `proxy.headers: xforwarded`; Operator-Ingress aus,
  eigener Ingress mit cert-manager; Operator-NetworkPolicy aus, stattdessen CiliumNetworkPolicies
  (default-deny + explizite Freigaben: Traefik→8080, Prometheus→9000, Operator→8080/9000,
  Keycloak→CNPG 5432). **Kein** Egress ins Internet – kein aktiver Identity-Provider, kein SMTP;
  der alte `identity_provider`-Eintrag im Realm `hopps` wird nach dem Umzug in der Admin-Konsole gelöscht.
- **Metriken**: `metrics-enabled`, der Operator legt den ServiceMonitor selbst an.
- **Pod-Security**: Der Operator setzt keinen `securityContext` (geprüft im Operator-Code 26.8.2);
  `unsupported.podTemplate` injiziert `runAsNonRoot`/`seccompProfile`/`drop ALL`. „unsupported" heißt:
  bei Operator-Upgrades den Status der CR auf Warnungen prüfen.
- **Update-Strategie** `RecreateOnImageChange` (1 Instanz, kein Rolling Update); `instances: 2` erst
  beim 3. Node – der Cluster-Cache läuft per JDBC-Ping, Policy für JGroups (7800/57800) liegt schon bei.
- Ressourcen: 768Mi/1Gi (captain-kube nutzt ~640Mi bei 768Mi Limit; Keycloak 26 nimmt 70 % des
  Limits als Heap).
- Kein `bootstrapAdmin`: Admin `hummli` kommt mit den Daten aus captain-kube.

**Rollout in zwei Schritten.** Während der Parallelphase bleibt captain-kube das produktive Keycloak –
im Realm `hopps` können weiter User dazukommen. Der erste Dump dient nur dem Test-Rollout auf karibik,
beim Cutover wird der Stand **noch einmal** komplett übertragen. Keycloak 26.3.1 → 26.8.x: Die
Schema-Migration macht Keycloak selbst beim ersten Start – und genau deshalb darf der zweite Restore
nicht in die bereits migrierte DB laufen (neue Tabellen, `MIGRATION_MODEL` fortgeschrieben → Konflikte),
sondern nur in eine geleerte.

*Schritt A – Test-Rollout (jetzt, captain-kube läuft weiter):*

1. Push → `keycloak-operator-source` und `keycloak-operator` werden grün (`kubectl -n keycloak get deploy`,
   `kubectl get crd keycloaks.k8s.keycloak.org`). `keycloak` bleibt **suspended**.
2. Dump auf captain-kube, **ohne** Keycloak dort zu stoppen (Konsistenz pro Dump reicht für den Test):
   `kubectl -n postgres-cluster exec postgres-cluster-0 -c postgres -- pg_dump -Fc -U postgres keycloak > keycloak.dump`
3. Restore in den CNPG-Primary auf karibik (DB `keycloak` existiert leer, Rolle `keycloak`; Primary per
   `kubectl get cluster -n cnpg-cluster` ermitteln):
   `kubectl -n cnpg-cluster exec -i postgres-cluster-1 -c postgres -- pg_restore -U postgres -d keycloak --no-owner --role=keycloak < keycloak.dump`
4. `suspend: false` in `infrastructure.yaml`, push. Prüfen: `kubectl -n keycloak get keycloak keycloak -o yaml`
   (Conditions `Ready=True`, keine `HasErrors`), Login unter `https://auth.karibik.hummler.com`,
   Realm `hopps` samt Clients vorhanden, Metriken in Prometheus.
5. Im Realm `hopps` auf **captain-kube** (produktiv!) die Redirect-URIs für die karibik-Hosts ergänzen
   (oauth2-proxy, Grafana) – so kommen sie mit dem finalen Dump automatisch mit. Auf karibik nur testen,
   nichts konfigurieren, was erhalten bleiben soll: der Stand dort wird beim Cutover überschrieben.

**Schritt A erledigt am 2026-11-08:** Dump 252 KB (PG 16.9, `pg_dump` 17.5 aus dem Spilo-Pod), Restore
mit 30 ignorierten Fehlern – alle Spilo-Beiwerk (Schema `metric_helpers`, Rollen `admin`/`robot_zmon`,
Extension-Views `pg_stat_statements`/`pg_stat_kcache`), alle 88 Keycloak-Tabellen übernommen. Keycloak
26.8.2 migrierte beim ersten Start 26.3.1 → 26.4.0 → … → 26.8.2 (101 Tabellen), 0 Fehler, 7 User, beide
Realms. `unsupported.podTemplate` greift (Pod läuft unter `restricted`), Operator legt ServiceMonitor an,
Issuer `https://auth.karibik.hummler.com/realms/hopps`, Admin-/Account-Konsole erreichbar.

**Stolperstein cert-manager hinter default-deny:** Das Zertifikat blieb `pending` („self check … timeout"),
Hubble zeigte `traefik → cm-acme-http-solver:8089 Policy denied`. `base/ciliumNetworkPolicy` enthält
bewusst nur `default-deny`; jede App **mit Ingress** muss `../../base/ciliumNetworkPolicy/allow-acme.yaml`
zusätzlich einbinden (Konvention wie traefik, oauth2-proxy, kube-prometheus-stack, minio). Die Policy in
die Base aufzunehmen bricht deren Builds („already registered id") – auf **beiden** Clustern, da sie
dieselbe Base lesen.

**Stolperstein Realm-Import hinter default-deny (2026-11-08, Pro Makler):** Die Import-Jobs des Operators
(`KeycloakRealmImport`) laufen als eigene Pods mit `app=keycloak-realm-import` und brauchen eine eigene
Egress-Freigabe zu CNPG (`allow-egress-from-realm-import-to-postgres` in `karibik/keycloak/networkPolicy.yaml`),
sonst scheitert der Job mit „Connect timed out“.

*Schritt B – finaler Sync beim Cutover (Keycloak auf captain-kube wird abgeschaltet):*

1. captain-kube: Keycloak auf 0 skalieren (`kubectl -n keycloak scale sts keycloak --replicas=0`; das
   Bitnami-HelmRelease dort vorher suspenden, sonst skaliert Flux zurück). Ab jetzt keine Änderungen mehr.
2. Dump wie in A.2 – das ist der endgültige Stand.
3. karibik: Keycloak stoppen, ohne Commit: Flux-Kustomization `keycloak` suspenden
   (`flux suspend kustomization keycloak`) und die CR auf 0 Instanzen patchen
   (`kubectl -n keycloak patch keycloak keycloak --type merge -p '{"spec":{"instances":0}}'`).
4. DB leeren und neu einspielen (als `postgres`, DB `keycloak` bleibt bestehen, nur das Schema wird ersetzt):
   `psql -U postgres -d keycloak -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO keycloak;'`
   dann `pg_restore` wie in A.3. Keycloak migriert beim nächsten Start erneut von 26.3.1 auf 26.8.x.
5. `flux resume kustomization keycloak` – Flux setzt `instances: 1` aus Git zurück. Prüfen wie A.4.
6. DNS `auth.kube.hummler.com` (und die extraHosts `auth.hopps.cloud`/`auth.hopps.app` im Ingress
   ergänzen, jetzt kann cert-manager sie ausstellen) → karibik. Danach Keycloak auf captain-kube entfernen.

Der jeweilige Dump ist zugleich das Rollback: captain-kube bleibt bis Schritt B unverändert, und auch
in B kann der alte StatefulSet wieder auf 1 skaliert werden, solange dort nichts gelöscht wurde.

## 4d. Phase 4 – Control-Plane auf private IPs umstellen (erst nach dem 3. Node)

> **Voraussetzung:** Alle Apps sind von captain-kube migriert und captain-kube ist als 3. Node in
> karibik gejoint. Vorher **nicht** anfassen: etcd hat mit 2 Membern keine Fehlertoleranz – jeder
> Member-Neustart kostet das Quorum, ein Fehler an der Peer-URL heißt etcd-Handarbeit.

**Ist-Stand (2026-11-04):** Pod-Verkehr zwischen den Nodes läuft bereits über den Cilium-VXLAN-Tunnel
auf den privaten IPs (kubelet `--node-ip=10.0.1.3`, LB-Targets privat). Nur die Control-Plane
advertised die **öffentlichen** IPs, weil kubeadm beim Init ohne `advertiseAddress` die IP der
Default-Route nimmt:

| Komponente | heute | Ziel |
| --- | --- | --- |
| `kube-apiserver --advertise-address` | 116.202.230.235 / 88.99.225.74 | 10.0.1.3 / 10.0.0.2 |
| etcd `listen-peer-urls`, `initial-advertise-peer-urls` | `https://<public>:2380` | `https://<privat>:2380` |
| etcd `listen-client-urls`, `advertise-client-urls` | `https://127.0.0.1:2379,https://<public>:2379` | `https://127.0.0.1:2379,https://<privat>:2379` |
| apiserver-Zertifikat IP-SANs | 172.20.0.1, public, 142.132.246.190 | + private Node-IP |
| nftables Regel 8 (`node_pub_ips` → 2379/2380/6443) | nötig | entfällt, Ports öffentlich komplett zu |

**Warum das mehr ist als Firewall-Kosmetik:** Die `kubernetes`-Endpoints sind die öffentlichen IPs.
Cilium hängt das Label `kube-apiserver` nur an Node-Identitäten, deren IPs zu den Endpoints passen –
auf karibik passt keine (Node-InternalIP ist privat). Deshalb trägt der Webhook-Aufruf des apiservers
vom anderen Node nur `remote-node` (siehe 4b, Stolperstein Webhook). Mit privaten Endpoints greift die
Entity `kube-apiserver` wieder und der karibik-Patch wird überflüssig.

**Ablauf (Wartungsfenster ~1 h, rollierend Node für Node, mit 3 etcd-Membern bleibt das Quorum):**

0. `etcdctl snapshot save` auf einem Node; `kubectl get nodes` und `etcdctl member list` festhalten.
1. **kubeadm-config** (`kubectl -n kube-system edit cm kubeadm-config`): `apiServer.certSANs` um die
   privaten Node-IPs ergänzen, `controlPlaneEndpoint` bleibt `142.132.246.190:6443`.
2. **Pro Node, nacheinander:**
   1. Zertifikate neu: `kubeadm init phase certs apiserver --config <cfg>` sowie
      `kubeadm init phase certs etcd-server` / `etcd-peer` mit der privaten IP als
      `localAPIEndpoint.advertiseAddress` (alte Zertifikate vorher wegsichern).
   2. etcd-Peer-URL im Cluster umstellen: `etcdctl member update <ID> --peer-urls=https://<privat>:2380`.
   3. `/etc/kubernetes/manifests/etcd.yaml`: alle `*-urls` auf die private IP (127.0.0.1 für Client
      beibehalten), Annotation `kubeadm.kubernetes.io/etcd.advertise-client-urls` anpassen. etcd startet neu.
   4. `/etc/kubernetes/manifests/kube-apiserver.yaml`: `--advertise-address` und die Annotation
      `kube-apiserver.advertise-address.endpoint` auf die private IP. apiserver startet neu.
   5. Prüfen: `etcdctl member list` (Peer-URL privat, Member healthy), `kubectl get endpoints kubernetes`
      zeigt die private IP dieses Nodes, `kubectl get nodes` alle Ready.
3. Firewall: Regel 8 aus [nftables/karibik-node.nft](nftables/karibik-node.nft) entfernen, Ruleset
   neu laden, Verbindung über den LB prüfen (`kubectl --context karibik get nodes`).
4. Cilium: `kubectl -n cilium exec ds/cilium -- cilium ip-cache list | grep kube-apiserver` zeigt die
   privaten Node-IPs. Danach kann `infrastructure/karibik/cloudnative-pg/patch-webhook-policy.yaml`
   entfallen (optional – `host`/`remote-node` zuzulassen ist weiterhin korrekt).

**Lernpfad:** passt zu den offenen etcd-Themen (advertise vs. listen, Peer-URL/SAN-Zusammenhang,
`member update`) in [docs/learning/README.md](../learning/README.md).

## 4e. Hermes Agent (KI-Harness) – Neuinstallation auf karibik

[Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research) ist ein self-hosted
KI-Agent mit persistentem Memory, Skills, Messaging-Gateways (Telegram, Discord, …) und Web-Dashboard.
Er läuft **nur auf karibik** unter `https://hermes.hummler.com` – es gibt nichts von captain-kube zu
migrieren. DNS: `*.hummler.com` zeigt seit 2026-11-08 auf den karibik-LB (`2.31.111.21`); captain-kube
behält `*.kube.hummler.com` (geprüft: kein captain-kube-Ingress liegt direkt unter `hummler.com`).

### Entscheidungen (2026-11-08)

| Thema | Entscheidung | Begründung |
| ----- | ------------ | ---------- |
| Chart | Community-Chart [jyje/hermes-agent-helm](https://github.com/jyje/hermes-agent-helm) `1.23.0` als OCI (`oci://ghcr.io/jyje/hermes-agent-helm`) | Kein offizielles Upstream-Chart. Dieses ist das aktivste (Upstream-Tracking, `values-hardened.yaml` für PSS `restricted`, Device-Login-Init-Container). Image `nousresearch/hermes-agent`, Tag = `appVersion` des Charts (1.23.0 → `v0.22.5`; Upstream-Latest ist `v0.23.0`), Renovate bumpt Chart und Image gemeinsam. |
| Workload | 1 Pod (Deployment, `Recreate`), PVC 10Gi Longhorn (2 Replikate) unter `/opt/data` (anfangs 5Gi; der Lazy-Install legt eine 2,3-GB-Python-Umgebung unter `installs/` an) | Single-Writer: Memory, Skills und SQLite-Sessions liegen auf dem Volume. `replicaCount > 1` wird vom Chart abgelehnt. |
| Pod Security | Namespace `hermes` mit PSS `restricted`; Werte aus `values-hardened.yaml` (UID 10000, read-only Rootfs, tmpfs für `/run` und `/tmp`) | Das Image startet normalerweise als root (s6-overlay chown). Non-root ist laut Chart-CI gegen das Image verifiziert; `fsGroup: 10000` ersetzt das chown. |
| LLM-Provider | `openai-codex`: das ChatGPT-Plus-Abo per **Codex-OAuth** (Device-Code-Login beim Pod-Start, Init-Container `auth-device-login`), Modell `gpt-6-luna` | Entscheidung 2026-11-08 trotz Vorbehalten (siehe „Vorbehalte Codex-OAuth“ unten): kein zusätzlicher API-Key, Kosten im Abo. Das Plus-Abo enthält **keinen** API-Zugang, die Alternative `openai-api` mit Platform-Key steht unten. |
| Dashboard-Login | Hermes-eigene **Basic-Auth** (`HERMES_DASHBOARD_BASIC_AUTH_*`), später **OIDC gegen Keycloak** | Hermes erzwingt bei Bind auf 0.0.0.0 eine eigene Auth („fails closed“); kein oauth2-proxy nötig (den gibt es auf karibik noch nicht). OIDC erst nach dem Keycloak-Cutover, sonst ändert sich die Issuer-URL. |
| Terminal-Backend | `local` – der Agent führt Shell-Befehle **im eigenen Pod** aus | `docker` braucht einen Daemon/Socket (im Cluster nicht unterstützt). Der Pod ist die Sandbox: `restricted`, read-only Rootfs, kein ServiceAccount-Token, Cilium default-deny. |
| Egress | **minimal**: `auth.openai.com`, `chatgpt.com` (Codex-Login/-Refresh und Inference), `api.telegram.org`, dazu für die lokale Transkription `pypi.org`, `files.pythonhosted.org`, `huggingface.co`, `*.huggingface.co`, `hf.co`, `*.hf.co` bis drei Labels tief, für Google Workspace `accounts.google.com`, `oauth2.googleapis.com`, `www.googleapis.com`, `*.googleapis.com`, für Todoist `ai.todoist.net`, `todoist.com`, für GitHub `github.com`, `api.github.com`, `raw.githubusercontent.com`, `codeload.github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`, für GitLab `gitlab.com`, für Microsoft 365 `login.microsoftonline.com`, `graph.microsoft.com`, `*.sharepoint.com` (alle 443, toFQDNs) | Erweiterung Schritt für Schritt (Rezept unten). Web-Tools des Agenten funktionieren erst, wenn Internet freigegeben ist. |
| Sprachnachrichten | `stt.provider: local` (faster-whisper, Modell `small`, Sprache automatisch) | Self-hosted statt Cloud-STT (Groq/OpenAI bräuchten eigenen Key, OpenAI-STT läuft nicht über den Codex-Login). Node hat 16 Kerne/64 GB, Pod-Limit 2 Gi reicht für `small` (int8). |
| config.yaml | `bootstrap.overwrite: true` – das Chart schreibt `config.yaml` bei jedem Start aus dem HelmRelease neu | Git ist die Wahrheit; Laufzeit-Änderungen (z. B. `/model` im Chat) überleben keinen Pod-Neustart. Dauerhafte Änderungen gehören ins HelmRelease. |
| Python-Extras | Init-Container `install-extras` (Kopie von seed-config per postRenderer) führt bei jedem Start `hermes pm install --extra google --extra stt-whisper` aus und legt `/opt/data/bin/python` an | Deklarativ statt Handarbeit im Pod; nach Volume-Verlust baut es sich selbst wieder auf. Der Wrapper gibt Skill-Skripten in der Agenten-Shell das Hermes-Python (sonst System-Python ohne Bibliotheken). |
| Websuche | `config.web.search_backend: openai-native`: OpenAIs serverseitiges `web_search`-Tool über den Codex-Transport, kein API-Key, kein neuer Egress | Entscheidung 2026-11-11. Deckt nur die Suche ab (Treffer mit Snippets); das Lesen kompletter Seiten (`web_extract`) bleibt ohne Anbieter, dafür später Firecrawl oder Tavily (ein Egress-Host, API-Key in `hermes-secrets`). |
| GitHub | Personal Access Token (classic, Scopes `repo`, `workflow`, `read:org`) als `GITHUB_TOKEN` in `hermes-secrets`; der Init-Container `install-extras` installiert die `gh`-CLI (gepinnt, SHA256-geprüft) nach `/opt/data/bin` und schreibt `~/.git-credentials` und `~/.config/gh/hosts.yml` | Entscheidung 2026-11-11. Classic statt fine-grained, weil nur der classic Token *alle* Repos abdeckt, auf die das Konto Zugriff hat (fremde Konten, Orgs ohne Fine-grained-Freigabe). Hermes streicht `GITHUB_TOKEN` absichtlich aus jedem Subprozess, daher der Umweg über Dateien. |
| GitLab | Zweigleisig: offizieller Remote-MCP `https://gitlab.com/api/v4/mcp` (Katalog-Eintrag, OAuth über das Dashboard) für Issues, Merge Requests, Pipelines; dazu `GITLAB_TOKEN` (PAT, nur `read_repository`/`write_repository`) in `hermes-secrets` für git über HTTPS, vom Init-Container in `~/.git-credentials` eingetragen | Entscheidung 2026-11-11. Es gibt kein gebündeltes GitLab-Skill und keine `glab`-CLI im Image; der MCP ersetzt die API-Seite, der PAT bleibt auf Repo-Inhalte beschränkt. |
| Todoist | Offizieller Remote-MCP-Server `https://ai.todoist.net/mcp` aus dem Hermes-Katalog, deklarativ als `config.mcp_servers.todoist` im HelmRelease; OAuth 2.1 mit Dynamic Client Registration, Einwilligung einmalig über das Dashboard, Token auf dem Volume | Entscheidung 2026-11-11. Kein API-Token in Git/SOPS nötig, kein lokaler MCP-Prozess im Pod (nur HTTP). Scope `data:read_write` (Aufgaben, Projekte, Kommentare lesen und schreiben). |
| Composio | **Wieder entfernt** (2026-11-18, am Tag der Einrichtung). Der gehostete Meta-MCP `connect.composio.dev` war kurz als `config.mcp_servers.composio` angebunden (OAuth-DCR, Egress-Policy); der einzige konkrete Bedarf, SharePoint/OneDrive, läuft nun direkt als Spiegel im Cluster | Composio ist ein Zwischenhändler für Geschäftsdaten mit Tokens auf fremden Servern, dazu agentengerichtete Werbe-Anweisungen auf `composio.dev/hermes` und Remote-Ausführungs-Tools, die die Pod-Sandbox umgehen. Ohne Bedarf kein Grund, das offen zu lassen. Details unten unter „Composio (entfernt)“. |
| SharePoint/OneDrive | **Spiegel im Cluster** statt API-Zugriff: drei Sidecars `onedrive` (OneDrive des Kontos), `office` (Site `office`, Bibliothek „Dokumente“) und `familie` (Site `familie`, Bibliothek „Dokumente“) mit [abraunegg/onedrive](https://github.com/abraunegg/onedrive) (Image `driveone/onedrive:2.5.12-alpine`) im Hermes-Pod, Monitor-Modus in beide Richtungen, gemeinsame PVC `hermes-m365` (StorageClass `longhorn-cache`: 1 Replik, Recurring-Job-Gruppe `no-backup`), Hermes sieht die Dateien unter `/opt/onedrive`, `/opt/sharepoint/office` und `/opt/sharepoint/familie`. Login je Sidecar per Device-Code-Flow gegen die **eigene Entra-App** `hermes-onedrive` (Public Client), Konfiguration als ConfigMap `m365-sync` im Overlay | Entscheidung 2026-11-18. Hermes arbeitet mit lokalen Dateien (grep, Python, pandoc) deutlich besser als über Graph-Tool-Calls; der Client verhält sich wie der Desktop-OneDrive (inotify-Upload, Deltas alle 5 min). Kein Composio: Geschäftsdokumente nicht durch einen Zwischenhändler. Kein `rclone mount` (FUSE unter PSS `restricted` unmöglich), kein `rclone bisync` (Aufsatz statt Client). Keine zusätzliche Sicherung: OneDrive wird bereits gesichert, der Spiegel baut sich neu auf. Kein Löschschutz möglich (`no_remote_delete` gibt es nur upload-only) – Papierkorb in SharePoint ist das Netz. |
| Microsoft Graph | Eigenes Skill `m365-graph` (Python mit MSAL und requests aus der Hermes-Umgebung, kein MCP, kein Node) gegen eine **zweite Entra-App `hermes-graph`** mit delegierten Rechten `Files.Read.All`, `Sites.Read.All`, `Mail.ReadWrite`, `Calendars.ReadWrite`, `User.Read`, `offline_access` – bewusst **ohne `Mail.Send`** und ohne Datei-Schreibrechte. Skill-Dateien als ConfigMap im Overlay, `install-extras` kopiert sie nach `skills/productivity/m365-graph/` | Entscheidung 2026-11-18. Ergänzt den Spiegel um das, was dort nicht liegt (750 GB OneDrive, Metadaten, Versionen) und um Outlook. Prinzip „Graph liest, der Spiegel schreibt“: ein Graph-Token mit `Files.ReadWrite.All` gäbe dem Agenten Löschzugriff auf das gesamte OneDrive. Mail: Entwürfe ja, Senden nein (Graph trennt das sauber über `Mail.Send`); `Mail.ReadWrite` erlaubt technisch auch Löschen/Verschieben – Grenze ist das Recht, nicht das Skript. Kalender schreibend auf Wunsch. Kein neuer Egress (`login.microsoftonline.com`, `graph.microsoft.com` sind für die Sidecars offen). |
| Google Workspace | Skill `google-workspace` mit eigenem OAuth-Desktop-Client; Client-Secret als SOPS-Secret auf `/opt/data/google_client_secret.json` gemountet, Token `google_token.json` auf dem Volume | Entscheidung 2026-11-11. Volle Scopes (Gmail senden/ändern, Drive, Calendar, Sheets, Docs, Contacts) – Inhalte gehen beim Verarbeiten an OpenAI. Nur-Mail-Alternative wäre das `himalaya`-Skill mit App-Passwort. |
| Langzeit-Memory | **Hindsight** (self-hosted, Namespace `hindsight`) als Memory-Provider-Plugin; eingebautes Memory bleibt aktiv – siehe **4g** | Entscheidung 2026-11-18 nach Vergleich mit Honcho (Persönlichkeitsmodell, Cloud oder bezahlter API-Key), hwiki (Markdown, kein LLM) und den gehosteten Anbietern: Fakten-/Entitäts-Graph mit zeitlicher Suche passt zu einem Assistenten für Projekte, Server und Termine; ChatGPT-Abo nutzbar, offene UI, alles im Cluster. |
| Backup | **offen** – siehe TODO unten | karibik hat noch kein Longhorn-Backup-Target. |

### Aufbau

| Datei | Inhalt |
| --- | --- |
| [base/helm-repository/hermes-agent.yaml](../../infrastructure/base/helm-repository/hermes-agent.yaml) | OCI-HelmRepository (landet per Kustomize-`namespace` im Namespace `hermes`, Muster minio) |
| [base/apps/hermes/](../../infrastructure/base/apps/hermes/) | Namespace (PSS restricted), HelmRelease (`fullnameOverride: hermes`), CiliumNetworkPolicies, default-deny + allow-acme |
| [karibik/hermes/](../../infrastructure/karibik/hermes/) | Overlay + Secret `hermes-secrets` aus `hermes-env-encrypted.env` (SOPS); Vorlage `hermes-env.env.example` |
| `configs/secret-encrypted.env` | neuer Key `HERMES_DOMAIN=hermes.hummler.com` (der Host liegt nicht unter `${DOMAIN}`) |
| [clusters/karibik/infrastructure.yaml](../../clusters/karibik/infrastructure.yaml) | Kustomization `hermes` (dependsOn longhorn, traefik, cluster-issuer; Timeout 15m wegen 2,2-GB-Image) |

Alle Keys aus `hermes-secrets` werden 1:1 Umgebungsvariablen im Pod (`extraEnvFrom`), auch im Init-Container.
Das Chart kann sie deshalb nicht prüfen → `dashboard.auth.provider: external`. Der Chart-Platzhalter
`env.OPENAI_API_KEY` wird per `null` entfernt; das vom Chart gerenderte Secret `hermes-env` bleibt leer
(deshalb der abweichende Name `hermes-secrets`).

Der Init-Container `auth-device-login` (gleiches Hermes-Image) läuft unter PSS `restricted` als UID 10000
(`auth.deviceFlow.securityContext`), dieselbe UID wie der Agent, damit das chown der Token-Datei ein No-Op ist.
`readyNotify.enabled: true` schickt nach jedem Gateway-Start eine Einzeiler-Meldung per Telegram.

### Schritt 1 – Secrets befüllen (vor dem ersten Push)

```bash
sops infrastructure/karibik/hermes/hermes-env-encrypted.env   # öffnet entschlüsselt im Editor
```

| Key | Wert |
| --- | --- |
| `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `_PASSWORD` / `_SECRET` | bereits gesetzt (`hummli`, Zufallswerte) – Passwort per `sops -d … \| grep PASSWORD` nachsehen |
| `TELEGRAM_BOT_TOKEN` | siehe Schritt 2 |
| `TELEGRAM_ALLOWED_USERS` | deine numerische Telegram-User-ID (kommagetrennt für mehrere). Leer/Platzhalter = Bot antwortet niemandem. |
| `TELEGRAM_HOME_CHANNEL` | Chat-ID für Zustellungen (Device-Code beim Codex-Login, „Gateway läuft“, Cron). Für 1:1 = deine User-ID. **Vorher dem Bot `/start` schicken**, sonst darf er dir nicht schreiben. |

Kein `OPENAI_API_KEY` nötig: der Codex-Login läuft beim ersten Start (Schritt 3). Ohne gültigen
`TELEGRAM_BOT_TOKEN` würde der Init-Container den Device-Code nur ins Log schreiben und der Gateway ohne
Plattform starten; die Platzhalter `REPLACE_ME` deshalb vor dem Push ersetzen.

### Schritt 2 – Telegram-Bot einrichten

1. In Telegram **@BotFather** öffnen → `/newbot` → Name (z. B. „Hermes karibik“) und Username
   (muss auf `bot` enden, z. B. `hummler_hermes_bot`). BotFather antwortet mit dem Token
   (`123456789:AA…`) → `TELEGRAM_BOT_TOKEN`.
2. Eigene User-ID ermitteln: **@userinfobot** anschreiben (antwortet mit `Id: 123456789`) →
   `TELEGRAM_ALLOWED_USERS`. Optional dieselbe ID als `TELEGRAM_HOME_CHANNEL` eintragen, dann
   landen Cron-/Benachrichtigungen des Agenten in deinem Chat.
3. Optional in BotFather `/setprivacy` → Disable, falls der Bot später in Gruppen alle Nachrichten
   lesen soll (für 1:1-Chats nicht nötig).
4. Hermes nutzt **Long Polling** (nur ausgehend, `api.telegram.org:443`) – kein Webhook, kein Ingress,
   keine weitere Firewall-Regel.
5. **Vor dem Rollout** dem Bot einmal `/start` schicken: Telegram-Bots dürfen nur Nutzern schreiben, die den
   Chat eröffnet haben, und der Device-Code aus Schritt 3 kommt genau über diesen Weg. Antwortet der Bot
   später nicht, zuerst `TELEGRAM_ALLOWED_USERS`, dann die Egress-Policy prüfen
   (`hubble observe --from-pod hermes/ --verdict DROPPED`).

### Schritt 3 – Rollout, Codex-Login und Prüfung

```bash
git push
flux --context karibik reconcile kustomization flux-system --with-source
flux --context karibik get kustomization hermes
kubectl --context karibik -n hermes get helmrelease,pods,pvc,certificate,ingress
kubectl --context karibik -n hermes logs deploy/hermes -c auth-device-login -f
```

**Codex-Login (einmalig, interaktiv):** Der Pod bleibt in `Init:1/2`, bis der Login bestätigt ist. Der
Init-Container holt bei `auth.openai.com` einen Device-Code und schickt dir URL + Code per Telegram (steht
auch im Log oben). Im Browser, in dem du bei ChatGPT angemeldet bist, `https://auth.openai.com/codex/device`
öffnen, Code eingeben, bestätigen. Ein Code gilt ~15 Minuten, danach holt der Init-Container von selbst
einen neuen (Timeout 870 s, dann Neustart des Init-Containers). Das Refresh-Token liegt danach in
`/opt/data/auth.json`; bei späteren Pod-Starts wird nur validiert bzw. refresht, kein neuer Login.

Danach wie gewohnt: `kubectl … logs deploy/hermes -c hermes-agent -f`. Erwartung: HelmRelease `Ready` (ist es
wegen `install.disableWait` schon vor dem Login, sagt also nichts über den Pod),
Pod `1/1` (Readiness erst, wenn das Dashboard auf 9119 lauscht – der erste Start kopiert Skills aufs Volume
und dauert Minuten), Telegram-Meldung „Gateway läuft“ (readyNotify), Zertifikat `hermes-tls` Ready, Login
unter `https://hermes.hummler.com` mit Basic-Auth, Testnachricht im Dashboard, dann eine Nachricht an den Bot.

**Rollout erledigt am 2026-11-08:** Chart 1.23.0 / Hermes v0.22.5. Erster Install-Versuch scheiterte an der
Helm-Wartezeit (siehe Stolpersteine), zweiter Versuch mit `install.disableWait` lief durch. Codex-Device-Login
per Telegram zugestellt und bestätigt (vorher musste in ChatGPT → Einstellungen → Sicherheit die „Anmeldung per
Gerätecode“ für Codex freigeschaltet werden, sonst lehnt `auth.openai.com/codex/device` den Code ab),
`auth.json` auf dem Volume, Gateway im Polling-Modus mit Telegram verbunden, Dashboard auf 9119 mit
Let's-Encrypt-Zertifikat erreichbar (302 → `/login`). Harmlose Warnungen im Log: `/run belongs to uid 0`
(tmpfs-Mount, s6 läuft trotzdem), `chown /opt/data failed (rootless)` (fsGroup übernimmt), Telegram
„Discovering fallback IPs via DNS-over-HTTPS“ (DoH ist per default-deny geblockt, Verbindung über
`api.telegram.org` klappt trotzdem).

### Sprachnachrichten (Telegram) – lokale Transkription

Hermes transkribiert Telegram-Sprachnachrichten automatisch (`stt.enabled`, Default an) und reicht den Text
an den Agenten weiter. Provider ist **`local`** (faster-whisper im Pod), konfiguriert im HelmRelease unter
`config.stt`. Was dafür nötig war (2026-11-08):

- **faster-whisper ist nicht im Image.** Hermes installiert es beim ersten Bedarf per gebündeltem `uv` nach
  `/opt/data/installs` (Lazy-Install, `security.allow_lazy_installs` Default an). Dafür Egress zu `pypi.org`
  und `files.pythonhosted.org`.
- **Whisper-Modell** `small` (~500 MB) kommt von `huggingface.co` nach `/opt/data/.cache/huggingface`. Die
  Dateien selbst liefert das CDN unter `hf.co` (Redirect auf z. B. `us.aws.cdn.hf.co`, auch mit
  `HF_HUB_DISABLE_XET=1`); Ciliums `*` in `matchPattern` matcht nur ein Label, daher `*.hf.co`, `*.*.hf.co`,
  `*.*.*.hf.co`. Fehlerbild ohne diese Freigabe: `httpx.ConnectTimeout`, Hubble zeigt Drops zu einer AWS-IP.
  Beides liegt auf der PVC, Download also einmalig (bis zum Volume-Verlust, siehe Backup-TODO).
- `ffmpeg` ist im Image (Konvertierung der Telegram-OGG-Dateien), Audio-Cache unter `/opt/data/cache/audio/`.
- **Nach einem Lazy-Install braucht der Gateway einen Neustart** (`pm.extras`: „restart needed“), erst dann läuft
  er aus der neuen Umgebung unter `/opt/data/installs/<hash>/environments/<id>/venv` (in `facts.json` als
  „committed“ eingetragen; `hermes doctor` zeigt „Runtime venv staged … active in this process“). Ein Pod-Neustart
  genügt. Vorab-Installation und Modell-Download von Hand: `hermes pm install --extra stt-whisper` im Pod, dann
  das Modell mit dem Python der neuen Umgebung laden (`tools.transcription_local._load_local_whisper_model`).
- Boot-Warnung `[stage2] Warning: dependency refresh failed` ist rootless-bedingt (der Hook ruft `s6-setuidgid`
  als Nicht-root) und harmlos: `pm.recovery.refresh_dependencies` liefert manuell „current“.
- Sprache leer = Auto-Erkennung (deutsch/englisch gemischt). `base` wäre schneller, aber für Deutsch merklich
  schlechter; `medium`/`turbo` sprengen das 2-Gi-Limit des Pods.

Testen: Sprachnachricht an den Bot schicken. Die erste dauert länger (Install + Modell-Download), danach
Sekunden. Sprach-Antworten (TTS) sind getrennt: im Telegram-Chat `/voice on` (Antwort nur auf Sprachnachrichten
per Sprache) oder `/voice tts`; Default-Provider `edge` bräuchte Egress zu Microsoft – nicht freigegeben.

Prüfen bei Problemen: `kubectl -n hermes logs deploy/hermes -c hermes-agent | grep -iE "stt|whisper|transcri"`,
Drops per Hubble (`--verdict DROPPED`), Installationsstand `hermes pm` im Pod.

### Google Workspace (Gmail, Calendar, Drive, Sheets, Docs)

Das gebündelte Skill `google-workspace` (`/opt/hermes/skills/productivity/google-workspace/`) spricht die Google-APIs
mit einem eigenen OAuth-Desktop-Client. Es gibt keine config.yaml-Keys und keine Env-Variablen, nur zwei Dateien
in `HERMES_HOME`: `google_client_secret.json` (aus der Google Cloud Console) und `google_token.json` (Refresh-Token,
entsteht beim einmaligen Login, erneuert sich selbst).

**Warum das aus dem Chat heraus zuerst scheiterte (2026-11-10):** Die Terminal-Shell des Agenten bekommt von Hermes
bewusst einen Standard-PATH; `python` ist dort `/usr/bin/python` ohne Google-Bibliotheken, `hermes` nicht auffindbar.
`setup.py --install-deps` kann außerdem nur aus der aktiven Hermes-Umgebung installieren.

**GitOps-Teile (Commit 2026-11-11):**

- Python-Extra `google` (google-api-python-client, google-auth-oauthlib …) über den Init-Container `install-extras`
  (siehe Entscheidungen). Der Container ist per JSON-Patch `copy` eine Kopie von `seed-config`, damit er automatisch
  dasselbe Image wie der Agent nutzt; Indizes der Init-Container stehen im HelmRelease-Kommentar.
- `/opt/data/bin/python` (plus `python3`): Wrapper auf das Python der aktiven pm-Umgebung mit `/opt/hermes` im
  Modulpfad. **Stolperstein:** Das Terminal-Tool startet `bash -lic`, und Debians `/etc/profile` setzt den PATH
  dabei auf `/usr/local/bin:/usr/bin:/bin:…` zurück – alles, was Hermes an Pfaden zusammenbaut (`$HERMES_HOME/bin`,
  Tool-Verzeichnisse), ist in der Agenten-Shell weg, `hermes` nicht auffindbar, `python` = System-Python. Deshalb
  schreibt `install-extras` zusätzlich `/opt/data/.bash_profile` (HOME des Hermes-Users), das `/opt/data/bin` und
  `/opt/hermes/bin` voranstellt. Prüfen: `hermes chat -q "Fuehre aus: command -v python; echo \$PATH"`.
- Client-Secret: [karibik/hermes/google-client-secret-encrypted.yaml](../../infrastructure/karibik/hermes/google-client-secret-encrypted.yaml)
  (Secret `hermes-google-client`, SOPS), per `extraVolumes` als Datei nach `/opt/data/google_client_secret.json`
  (subPath, read-only). Rotation: neues Secret in der Console erzeugen, Datei neu verschlüsseln, pushen.
- Egress: `accounts.google.com`, `oauth2.googleapis.com`, `www.googleapis.com`, `*.googleapis.com` (443).

**Einmaliger OAuth-Login (per `kubectl exec`, nicht im Chat):**

```bash
kubectl --context karibik -n hermes exec -it deploy/hermes -c hermes-agent -- sh -c 'cd /opt/data && GS=/opt/hermes/skills/productivity/google-workspace/scripts/setup.py; /opt/data/bin/python $GS --check; /opt/data/bin/python $GS --auth-url'
```

URL im Browser öffnen (als das Google-Konto, das in der Console als Testnutzer steht), einwilligen. Der Redirect auf
`http://localhost:1/...` schlägt im Browser fehl – das ist so gedacht. Die komplette Adresszeile (oder nur den
`code=`-Wert) kopieren und eintragen:

```bash
kubectl --context karibik -n hermes exec -it deploy/hermes -c hermes-agent -- sh -c 'cd /opt/data && /opt/data/bin/python /opt/hermes/skills/productivity/google-workspace/scripts/setup.py --auth-code "<URL-oder-Code>"'
```

`--check` muss danach `AUTHENTICATED` melden. Google-Seite (einmalig): Projekt, APIs Gmail/Calendar/Drive/Sheets/Docs/
People aktiviert, OAuth-Client vom Typ „Desktop app“, eigenes Konto als Testnutzer, solange die App im Status
„Testing“ ist (Tokens laufen dort nach 7 Tagen ab, bis die App „In production“ ist).

**Login erledigt am 2026-11-11:** erster Versuch scheiterte mit `403 access_denied`, weil das Google-Konto nicht als
Testnutzer der OAuth-App eingetragen war (Console → Zielgruppe → Testnutzer). Danach `--auth-code` OK,
`--check-live` OK, `google_token.json` auf 0600 gesetzt (das Skript legt sie mit 0644 an).
Aktivierte APIs im Google-Projekt (Stand 2026-11-11): Gmail, Calendar, People. **Drive, Sheets und Docs bewusst
nicht aktiviert** – der Scope steckt zwar im Token, Aufrufe enden aber mit `403 … API has not been used in project`,
der Agent bekommt also nur diese Fehlermeldung. Wer Drive später will: API in der Console aktivieren, kein neuer Login.

**Hinweise:** Das Setup fordert alle Scopes an (`--services` aus der Skill-Doku existiert im Skript 1.2.0 nicht).
Das Token liegt nur auf dem Volume (Backup-TODO); nach Volume-Verlust den Login wiederholen. Das Client-Secret war
einmal über den Chat hochgeladen worden (Modell-Kontext) – bei Gelegenheit in der Console rotieren.

### Todoist (Aufgaben) – Remote-MCP mit OAuth

Hermes bringt einen kuratierten MCP-Katalog mit (`/opt/hermes/optional-mcps/`, Nous-geprüft). Der Eintrag `todoist`
ist der **offizielle, von Todoist gehostete** MCP-Server `https://ai.todoist.net/mcp` (reines HTTP, Hermes startet
dafür keinen Prozess). Statt `hermes mcp install todoist` im Pod steht der Block deklarativ im HelmRelease
(`config.mcp_servers.todoist`, landet per `bootstrap.overwrite` in `config.yaml`), inklusive der Tool-Ausschlüsse
aus dem Katalog-Manifest. Authentifizierung ist OAuth 2.1 mit Dynamic Client Registration: der Pod registriert sich
selbst bei `todoist.com/oauth/register` mit der Callback-URL `https://hermes.hummler.com/api/mcp/oauth/callback/todoist`
(aus `dashboard.public_url`). Darum braucht es **kein Secret in Git** – es gibt nichts vorab zu hinterlegen.

**Einmalige Einwilligung (nach dem Rollout):**

1. `https://hermes.hummler.com` → **MCP** → Server `todoist` → **Connect** (bzw. *Authorize*). Das Dashboard
   startet den Flow im Gateway und öffnet die Todoist-Autorisierungsseite.
2. Bei Todoist anmelden und den Zugriff bestätigen. Todoist leitet auf die Callback-URL zurück; der Pfad ist vom
   Dashboard-Login ausgenommen, der Flow ist an eine `state`-Prüfung gebunden.
3. Das Dashboard meldet die gefundenen Tools. Danach **den Pod neu starten** (`kubectl --context karibik -n hermes
   rollout restart deploy/hermes`): Der laufende Gateway hatte den Server beim Start mangels Token „geparkt“ und
   belebte ihn nach der Einwilligung auch nach Minuten nicht wieder (Stand 2026-11-11, Chart 1.23.0); beim Start
   verbindet er mit den gespeicherten Tokens sofort. Dann in Telegram `/new`.

**Stolperstein Hosted Connectors:** Ohne geladene MCP-Tools griff der Agent zum Tool `manage_connections`
(Nous-gehostete „Hosted Connectors“ über das Nous-Portal, Todoist gibt es dort auch) und meldete „Verbindung zum
Connector-Dienst fehlgeschlagen“ – das war die Egress-Sperre zu Nous, nicht der MCP. Deshalb steht im HelmRelease
`config.tools.connectors.enabled: false`; das Tool taucht damit gar nicht mehr im Schema auf.

Token-Dateien: `/opt/data/mcp-tokens/todoist.json` (Access/Refresh-Token, erneuert sich selbst),
`todoist.client.json` (der dynamisch registrierte Client), `todoist.meta.json`. Liegen nur auf dem Volume
(Backup-TODO); nach Volume-Verlust Schritt 1–3 wiederholen. Trennen: Dateien löschen oder in Todoist →
Einstellungen → Integrationen den Zugriff widerrufen.

**Prüfen:** `kubectl --context karibik -n hermes exec deploy/hermes -c hermes-agent -- sh -c 'HOME=/opt/data hermes mcp list'`
(zeigt den Server und den Auth-Status) und im Chat „Welche Aufgaben habe ich heute in Todoist?“.

**Fallback ohne Dashboard:** `kubectl … exec -it … -- sh -c 'HOME=/opt/data hermes mcp login todoist'` druckt die
Autorisierungs-URL; der Redirect auf `127.0.0.1:<port>` schlägt im Browser fehl, die komplette Adresszeile dann in
die Eingabeaufforderung einfügen (Paste-Fallback). Egress wie beim Dashboard-Weg.

### Web-Recherche

Hermes wählt den Suchanbieter über `web.search_backend` (bzw. `web.backend` für Suche und Extraktion gemeinsam).
Ohne Konfiguration tastet es sich durch eine Kaskade aus API-Keys (`TAVILY_API_KEY`, `PERPLEXITY_API_KEY`,
`EXA_API_KEY`, `PARALLEL_API_KEY`, `KEENABLE_API_KEY`, `FIRECRAWL_API_KEY`, `SEARXNG_URL`, `BRAVE_SEARCH_API_KEY`) und
landet sonst bei Firecrawl, das ohne Key nicht funktioniert – so stand der Agent bis 2026-11-11 ohne Websuche da.

**Gewählt: `openai-native`.** Das Plugin `plugins/web/openai_native` führt selbst keine Suche aus, sondern lässt den
Codex-Transport OpenAIs serverseitiges `web_search`-Tool deklarieren; das Modell sucht dann bei OpenAI. Braucht
nur den bestehenden Codex-Login, keinen Key, keinen weiteren Egress (alles über `chatgpt.com`). Grenzen: nur Suche,
keine Extraktion ganzer Seiten (`web_extract` meldet „kein Provider“); Inhalte der Suche laufen über OpenAI wie
alles andere auch.

**Ausbau (optional):** Für `web_extract` einen gehosteten Anbieter ergänzen, bevorzugt Tavily (Suche + Extraktion,
kostenloses Kontingent monatlich) oder Firecrawl: Key als `TAVILY_API_KEY` bzw. `FIRECRAWL_API_KEY` in
`hermes-secrets`, `web.extract_backend: tavily|firecrawl` im HelmRelease, Egress `api.tavily.com` bzw.
`api.firecrawl.dev` (443). Die Suche kann dabei bei `openai-native` bleiben.

**Prüfen:** `hermes chat -q "Suche im Web: Wann ist der nächste Hessische Ehrenamtskongress? Nenne Datum und Quelle."`

### GitHub (Repos, PRs, Issues, CI)

Das gebündelte Skill `github` (`/opt/hermes/skills/software-development/github/`) arbeitet mit der `gh`-CLI und
fällt auf `git` plus REST zurück. Hermes selbst nutzt `GITHUB_TOKEN` für den Skills-Hub und den Modellkatalog.
Zwei Eigenheiten bestimmen den Aufbau: Das Image bringt **kein `gh`** mit, und Hermes **streicht `GITHUB_TOKEN`
aus der Umgebung jedes Subprozesses** (Tier-1-Secret in `tools/environments/local_env_policy.py`), auch aus der
Agenten-Shell. Deshalb erledigt der Init-Container `install-extras` beides deklarativ bei jedem Start:

- `gh` in gepinnter Version (`GH_VERSION`/`GH_SHA256` im HelmRelease) nach `/opt/data/bin/gh`, Download nur,
  wenn die Datei fehlt oder eine andere Version hat. Bump von Hand: neue Version und Checksumme aus
  `gh_<version>_checksums.txt` des Releases eintragen (Renovate kennt diesen Pin nicht).
- Aus `GITHUB_TOKEN` die Dateien `~/.git-credentials` (git, `credential.helper store`) und
  `~/.config/gh/hosts.yml` (gh) in **`/opt/data/home`**, Modus 0600; ohne Token werden sie entfernt. Dazu
  `~/.gitconfig` mit Identität `Hermes Agent <manuel@hummler.com>`, so sind Commits des Agenten am Namen erkennbar.
- Egress-Policy `allow-egress-to-github` (Hosts siehe Entscheidungstabelle).

**Stolperstein beim ersten Rollout (2026-11-11):** Die Flux-Kustomization `hermes` läuft mit `postBuild.substituteFrom`
(envsubst, strict). Jede `${VAR}`-Schreibweise in Init-Scripten im HelmRelease wird dabei als Cluster-Variable
gelesen: unbekannte brechen den Build ab („variable not set: GH_VERSION“, die alte Version bleibt stehen), Formen
wie `${PYTHONPATH:+…}` werden still verstümmelt (so war der Python-Wrapper mit `:$PYTHONPATH` gelandet). In
Scripten deshalb nur `$VAR` / `"$VAR"` und `printenv`, nie `${VAR}`.

**Stolperstein zwei (2026-11-12): HOME der Agenten-Shell ist `/opt/data/home`.** Hermes gibt Subprozessen im
Container nicht sein eigenes HOME (`/opt/data`, dort liegen `config.yaml`, Memory, Sessions), sondern ein
abgetrenntes „profile home“ `$HERMES_HOME/home` (`hermes_constants.get_subprocess_home`, Modus `auto`; mit
`TERMINAL_HOME_MODE=real` ließe sich das abschalten, wir folgen dem Standard). Die Shell-Init-Dateien
(`~/.bash_profile` usw.) sourct das Terminal-Tool dagegen aus `/opt/data`, deshalb stimmt der PATH, aber alles
HOME-relative (`.git-credentials`, `.gitconfig`, `.config/gh/`, `.npm`) muss nach `/opt/data/home`. Der erste
Aufbau hatte die Dateien unter `/opt/data` abgelegt; `git ls-remote` per `kubectl exec … HOME=/opt/data` sah
deshalb grün aus, während der Agent selbst „nicht angemeldet“ meldete. Beim Prüfen im Pod immer
`HOME=/opt/data/home` setzen oder gleich den Agenten fragen (siehe unten).

**Token anlegen (einmalig, GitHub-Seite):** <https://github.com/settings/tokens> → „Generate new token (classic)“,
Name `hermes-karibik`, Ablauf z. B. 90 Tage, Scopes **`repo`** (alle Repos lesen/schreiben, PRs), **`workflow`**
(Actions-Workflows ändern/auslösen), **`read:org`** (Org-Repos auflisten). Ein fine-grained Token wäre feiner
steuerbar, deckt aber nur Repos des eigenen Kontos und freigebender Orgs ab, nicht Repos fremder Konten, auf die
du als Collaborator Zugriff hast. Nur lesen? Dann statt `repo` nur `public_repo` plus `read:org`.

**Token hinterlegen:**

```bash
sops infrastructure/karibik/hermes/hermes-env-encrypted.env   # Zeile GITHUB_TOKEN=ghp_... ergänzen, speichern
git commit -am "hermes: GITHUB_TOKEN" && git push
flux --context karibik reconcile kustomization flux-system --with-source && flux --context karibik reconcile kustomization hermes
kubectl --context karibik -n hermes rollout restart deploy/hermes   # Secret-Aenderung allein startet den Pod nicht neu
```

**Prüfen:** `kubectl --context karibik -n hermes logs deploy/hermes -c install-extras` muss „gh … installiert/vorhanden“
und „GitHub-Credentials … geschrieben“ zeigen; dann im Chat „Prüfe mit gh auth status, ob du bei GitHub angemeldet
bist, und liste meine fünf zuletzt geänderten Repos.“ Hermes-seitig zusätzlich `hermes doctor` (prüft den Token
gegen `api.github.com`).

**Sicherheit:** Der Token kann in *allen* Repos des Kontos pushen. Der Agent verarbeitet fremde Inhalte (Mails,
Webseiten), Prompt-Injection ist damit ein reales Risiko für die Repos. Ablaufdatum setzen, bei Verdacht unter
<https://github.com/settings/tokens> widerrufen, Rotation = neue Zeile in der SOPS-Datei plus Pod-Neustart.

### GitLab (Issues, Merge Requests, Pipelines, Repos)

Für gitlab.com zwei Wege, weil Hermes anders als für GitHub kein Skill und keine CLI mitbringt:

1. **Remote-MCP** `config.mcp_servers.gitlab` (Katalog-Eintrag `/opt/hermes/optional-mcps/gitlab`, URL
   `https://gitlab.com/api/v4/mcp`, Scope `mcp`): Issues, Merge Requests, Pipelines, Repo-Kontext als Tools.
   OAuth 2.1 mit Dynamic Client Registration gegen `gitlab.com/oauth/*`, Einwilligung wie bei Todoist:
   Dashboard → **MCP** → `gitlab` → **Connect**, bei GitLab bestätigen, danach **Pod neu starten**
   (`kubectl --context karibik -n hermes rollout restart deploy/hermes`), dann `/new` in Telegram. Token unter
   `/opt/data/mcp-tokens/gitlab*.json` (Backup-TODO).
2. **git über HTTPS** für clone/push: `GITLAB_TOKEN` in `hermes-secrets`; `install-extras` hängt
   `https://oauth2:<token>@gitlab.com` an `~/.git-credentials` an (gemeinsame Datei mit GitHub, 0600).

**PAT anlegen:** <https://gitlab.com/-/user_settings/personal_access_tokens> → Name `hermes-karibik`, Ablauf
(gitlab.com erzwingt maximal 1 Jahr), Scopes **`read_repository`** und **`write_repository`**. Bewusst nicht `api`:
die API-Seite macht der MCP mit eigenem, widerrufbarem OAuth-Token; der PAT kann damit nur Repo-Inhalte lesen und
pushen, keine Issues, Mitglieder oder Einstellungen ändern. Der Token gilt für alle Projekte, in denen das Konto
Mitglied ist (eigene, Gruppen wie `hummler-gbr`, Einladungen).

**Hinterlegen:** wie bei GitHub (`sops infrastructure/karibik/hermes/hermes-env-encrypted.env`, Zeile
`GITLAB_TOKEN=glpat-...`, pushen, Reconcile, `rollout restart`).

**Prüfen:** Init-Log „GitLab-Credentials für git geschrieben“, dann im Pod
`sh -c 'HOME=/opt/data/home git ls-remote --heads https://gitlab.com/hummler-gbr/pro-makler.git | head -2'`; MCP-Seite im Chat:
„Liste die offenen Merge Requests in hummler-gbr/pro-makler.“

### Composio (entfernt am 2026-11-18)

Composio war für wenige Stunden als Remote-MCP angebunden (`config.mcp_servers.composio` →
`https://connect.composio.dev/mcp`, `auth: oauth`, Egress-Policy `allow-egress-to-composio` für
`connect.composio.dev` und `login.composio.dev`, Einwilligung über Dashboard → MCP → Connect, Token unter
`/opt/data/mcp-tokens/composio*.json`). Auslöser war die Aufforderung „Set up Composio from composio.dev/hermes“
an den Agenten, die an der Egress-Sperre scheiterte; nach der deklarativen Einrichtung sah der Agent 11 Tools
(`COMPOSIO_SEARCH_TOOLS`, `MULTI_EXECUTE_TOOL`, `MANAGE_CONNECTIONS`, `PROXY_EXECUTE`, `REMOTE_BASH_TOOL`,
`REMOTE_WORKBENCH`, drei Skill-Tools, `SUBMIT_FEEDBACK`).

**Warum wieder raus:** Der einzige konkrete Bedarf (SharePoint/OneDrive) läuft jetzt als Spiegel im Cluster, siehe
nächster Abschnitt. Ohne Bedarf bleiben nur die Nachteile: Zwischenhändler für Geschäftsdaten mit OAuth-Tokens auf
Composio-Servern, Remote-Ausführungs-Tools, die die Pod-Sandbox umgehen, Telemetrie, und eine Setup-Seite, die
außer dem Einzeiler nur an Agenten gerichtete Werbe-Anweisungen enthält („Composio dem Browser vorziehen“).

**Aufräumen außerhalb von Git (nach dem Rollout):** `kubectl --context karibik -n hermes exec deploy/hermes
-c hermes-agent -- sh -c 'rm -f /opt/data/mcp-tokens/composio*'` und im Composio-Dashboard
(<https://dashboard.composio.dev>) die Verbindung bzw. das Konto löschen, damit der dort hinterlegte Refresh-Token
wertlos ist. Dann Pod-Neustart und `/new` im Chat.

**Falls es doch einmal gebraucht wird:** Rezept wie bei Todoist/GitLab (Remote-MCP mit OAuth-DCR, keine Secrets in
Git), zwei Egress-Hosts wie oben; die OAuth-Metadaten liegen unter `connect.composio.dev/.well-known/`. Dann von
Anfang an `tools.exclude` für `COMPOSIO_REMOTE_BASH_TOOL`, `COMPOSIO_REMOTE_WORKBENCH`, `COMPOSIO_PROXY_EXECUTE`
und `COMPOSIO_SUBMIT_FEEDBACK` setzen.

### SharePoint/OneDrive – Spiegel im Cluster (Sidecars `onedrive`, `office`, `familie`)

**Warum ein Spiegel:** Hermes soll Dokumente lesen, durchsuchen, auswerten und auch schreiben. Über die Graph-API
wäre jeder Dateizugriff ein Tool-Call; mit lokalen Dateien nutzt der Agent sein Terminal (grep, Python, pandoc).
Composio schied aus (Geschäftsdokumente durch einen Zwischenhändler, Tokens bei Composio), `rclone mount` braucht
FUSE (`SYS_ADMIN` + `/dev/fuse`, unter PSS `restricted` unmöglich), `rclone bisync` ist ein periodischer Aufsatz.
Der [Linux-OneDrive-Client von abraunegg](https://github.com/abraunegg/onedrive) ist dagegen ein echter
Sync-Client wie OneDrive auf dem Desktop: beide Richtungen, lokale Änderungen per inotify sofort hochgeladen,
Online-Änderungen per Delta alle 5 Minuten, Konflikte ohne Datenverlust (lokale Kopie wird umbenannt, Online-
Version geholt). Kein FUSE, kein root. Entscheidung 2026-11-18.

**Warum drei Instanzen:** ein Client synchronisiert genau ein Laufwerk, entweder das OneDrive des Kontos oder
eine SharePoint-Bibliothek (`drive_id`). Gewünscht sind das OneDrive und die Bibliotheken „Dokumente“ der Sites
`office` und `familie`. Die Alternative, Bibliotheken als Verknüpfung in „Meine Dateien“ einzuhängen und mit
`sync_business_shared_items` über eine Instanz zu holen, wurde verworfen (Entscheidung 2026-11-18, „Weg 2“):
mehrere Sidecars sind mehrere Tokens und Logins, aber auch unabhängige Zustände, und das Konto je Bibliothek
kann ein anderes sein. Eine weitere Bibliothek = Sidecar-Block kopieren, `<name>.config` anlegen und in der
ConfigMap eintragen, Verzeichnis in `prepare-home`, Mount bei `hermes-agent`.

**Warum Sidecar und kein eigener Namespace:** PVCs sind namespace-gebunden, und Hermes muss dieselben Dateien
sehen. Ein eigenständiger Sync-Dienst müsste den Spiegel per Longhorn-RWX (NFS-Share-Manager) teilen – über NFS
verliert der Client inotify (lokale Änderungen erst beim nächsten vollen Scan), und jeder weitere Konsument
teilt sich die Löschsemantik. Wiederverwendbar ist das Muster: StorageClass `longhorn-cache` (cluster-weit),
Config-Datei und Sidecar-Block lassen sich in jeden anderen Pod kopieren. Erst wenn mehrere Pods denselben
Spiegel brauchen, lohnt ein eigener Namespace mit RWX-Volume.

**Aufbau:**

- Drei Sidecars `onedrive`, `office`, `familie` im Hermes-Pod (`extraContainers` im HelmRelease, bis auf Name,
  `SYNC_NAME` und `subPath` identisch), Image `driveone/onedrive:2.5.12-alpine` gepinnt, UID 10000 wie Hermes,
  read-only Rootfs, `/tmp` als tmpfs. Der Docker-Entrypoint erkennt den Nicht-root-Start und ruft den Client
  direkt mit `--confdir /onedrive/conf --syncdir /onedrive/data --monitor` auf; ein Wrapper kopiert vorher
  `<SYNC_NAME>.config` (und optional `<SYNC_NAME>.sync_list`) aus der ConfigMap in das beschreibbare `conf/`,
  weil der Client dort `refresh_token` und `items.sqlite3` ablegt.
- Gemeinsame PVC [`hermes-m365`](../../infrastructure/karibik/hermes/pvc-m365.yaml) (120 Gi: SharePoint
  `office` + `familie` komplett ≈ 100 GB laut Nutzer, OneDrive nur `Bachelorarbeit`, Rest Sync-DBs und Luft;
  die ersten 10 Gi waren am 2026-11-18 nach einer Stunde voll; Longhorn erweitert online) mit
  StorageClass [`longhorn-cache`](../../infrastructure/karibik/hermes/storageClass.yaml): **eine Replik** und
  per `recurringJobSelector` fest in der Recurring-Job-Gruppe `no-backup`. Longhorn hängt Jobs der Gruppe
  `default` an alle Volumes *ohne* Gruppen-Label; dieses Volume bleibt damit auch außen vor, wenn später ein
  Backup-Job über die default-Gruppe kommt. Microsoft 365 wird bereits gesichert, der Spiegel ist Cache.
  Unterverzeichnisse `<name>/data` und `<name>/conf` legt der Init-Container `prepare-home` an
  (Longhorn-Wurzel gehört root, gleiche Lösung wie bei `/opt/data`).
- Hermes sieht nur die `data/`-Verzeichnisse: **`/opt/onedrive`**, **`/opt/sharepoint/office`**,
  **`/opt/sharepoint/familie`** (read-write), nicht `conf/` mit den Tokens. Bewusst außerhalb von `HERMES_HOME`,
  damit die Besitzer-Prüfung von Hermes nichts davon merkt.
- Konfiguration: [`m365/onedrive.config`](../../infrastructure/karibik/hermes/m365/onedrive.config),
  [`m365/office.config`](../../infrastructure/karibik/hermes/m365/office.config) und
  [`m365/familie.config`](../../infrastructure/karibik/hermes/m365/familie.config) im Overlay → ConfigMap
  `m365-sync` (keine Secrets: App-ID, Tenant-ID und `drive_id` sind keine Geheimnisse). Gesetzt:
  `use_device_auth`, `application_id`, `azure_tenant_id`, `drive_id` (office, familie), `skip_dotfiles`,
  `disable_upload_validation` (SharePoint „bereichert“ Office-Dateien nach dem Upload mit Metadaten, ohne die
  Option lädt der Client sie als „geändert“ wieder herunter), `monitor_interval 300` (Minimum des Clients),
  `monitor_fullscan_frequency 12`.
- Egress-Policy `allow-egress-to-microsoft-365`: `login.microsoftonline.com`, `graph.microsoft.com`,
  `*.sharepoint.com` (Tenant-Hosts: `its2morrow.sharepoint.com` für die Sites, `its2morrow-my.sharepoint.com`
  für OneDrive). Gilt für den ganzen Pod, also auch für den Agenten. inotify auf barbossa-kube: 518 513 Watches,
  128 Instanzen (geprüft 2026-11-18, Kernel 7.0) – drei Clients teilen sich das Kontingent der UID 10000.

**Kein Löschschutz:** `no_remote_delete` kennt der Client nur zusammen mit `--upload-only`. Bidirektional heißt:
löscht der Agent eine Datei unter einem der drei Pfade, ist sie online im Papierkorb (93 Tage). Wer das nicht
will, nimmt `download_only` (Lesen) plus einen expliziten Rückweg für einzelne Dateien.

**Wie der Agent davon erfährt:** Mounts allein sagen Hermes nichts. Der Gateway läuft mit Arbeitsverzeichnis
`/opt/data` (`terminal.cwd: "."`), und `agent/prompt_builder.py` (`build_context_files_prompt`) lädt von dort
genau eine Projektkontext-Datei in jeden System-Prompt, Reihenfolge `.hermes.md`/`HERMES.md` → `AGENTS.md` →
`CLAUDE.md` → `.cursorrules`; `SOUL.md` (Identität) kommt unabhängig davon immer dazu. Deshalb schreibt der
Init-Container `install-extras` bei jedem Start **`/opt/data/HERMES.md`** mit den drei Pfaden, der
Sync-Semantik (beide Richtungen, Löschen wirkt online) und Verhaltensregeln (Unterordner statt Wurzel, keine
Temp-Dateien, Löschen nur auf Wunsch). Text im HelmRelease, Git ist die Wahrheit; nach einer Änderung Pod-Neustart
und `/new` im Chat. Hermes prüft Kontextdateien auf Prompt-Injection und blockt Treffer – den Text sachlich
halten, keine „ignoriere …“-Formulierungen. Prüfen (verifiziert 2026-11-18, der Agent nennt alle drei Pfade und
die Löschregel ohne Tool-Aufruf): `kubectl … exec deploy/hermes -c hermes-agent -- sh -c 'cd /opt/data &&
HOME=/opt/data hermes chat -q "Welche Spiegel-Verzeichnisse kennst du?"'`. **Stolperstein:** ohne `cd /opt/data`
startet `kubectl exec` in `/opt/hermes` (Installationsbaum), und Hermes überspringt dort die Kontextdatei-Suche
absichtlich – der Agent sucht dann mit grep nach „Spiegel“ und findet nichts. Der Gateway (Telegram, Dashboard)
läuft in `/opt/data` und ist nicht betroffen.

**Einrichtung (einmalig):**

1. **App in Entra ID registrieren:** <https://entra.microsoft.com> → App-Registrierungen → Neu. Name z. B.
   `hermes-onedrive`, Kontotyp „Nur Konten in diesem Organisationsverzeichnis“ (Single Tenant). Nach dem
   Anlegen unter *Authentifizierung*: Plattform **Mobile und Desktopanwendungen** mit den Redirect-URIs
   `http://127.0.0.1:53100/` und `https://login.microsoftonline.com/common/oauth2/nativeclient` (der Client
   erwartet beide, sonst `AADSTS50011`), und **„Öffentliche Clientflows zulassen“ = Ja** (ohne das kein
   Device-Code). Unter *API-Berechtigungen* (Microsoft Graph, delegiert): `Files.ReadWrite`,
   `Files.ReadWrite.All`, `Sites.ReadWrite.All`, `offline_access`; dann „Administratorzustimmung erteilen“.
   Kein Client-Secret anlegen – es ist ein Public Client. Eine App reicht für alle Sidecars.
   **Erledigt 2026-11-18** per Azure-CLI (`az ad app create --sign-in-audience AzureADMyOrg
   --is-fallback-public-client true --public-client-redirect-uris …`, `az ad sp create`,
   `az ad app permission admin-consent`): App `hermes-onedrive`, Anwendungs-ID
   `08d2c8df-9de2-4ddb-8328-eed94f794100`, Tenant „Hummler GbR“ `7804793a-7aea-4b72-af44-80d502e33d8d`,
   Grant `AllPrincipals` mit allen vier Scopes. Die Zustimmung schlug direkt nach dem Anlegen einmal mit
   „application … has been removed“ fehl (Replikationsverzug), beim zweiten Versuch nach ~1 min ging sie durch.
2. **Bibliotheken bestimmen:** die `drive_id` ist die Graph-Drive-ID (`b!…`). Ohne Sidecar-Login per Graph:
   `az rest --url "https://graph.microsoft.com/v1.0/sites/its2morrow.sharepoint.com:/sites/<site>:/drives"`
   (die Azure-CLI darf Sites per Pfad lesen, aber nicht per Suche; `/sites?search=*` braucht ein Token der
   eigenen App über den Device-Code-Flow – so am 2026-11-18 gemacht, Skript lag nur im Scratchpad). Im Pod geht
   später auch `onedrive --confdir /onedrive/conf --get-sharepoint-drive-id '<Site-Name>'`. **Erledigt
   2026-11-18:** `office` → Bibliothek „Dokumente“ (`…/sites/office/Freigegebene Dokumente`; die zweite
   Bibliothek „Agent-Ressourcen“ bleibt draußen), `familie` → „Dokumente“ (`…/sites/familie/Freigegebene
   Dokumente`; die Site fehlte in der Suche trotz Besitzer-Rolle – Suchindex, Site vom 2026-10-20). IDs stehen
   in `office.config` und `familie.config`, App- und Tenant-ID in allen drei Dateien.
3. Pushen, Reconcile (`flux --context karibik reconcile ks hermes --with-source`). Der Pod startet neu mit
   vier Containern; Hermes selbst wartet nicht auf die Sidecars.
4. **Device-Login, je Sidecar einmal:** `kubectl --context karibik -n hermes logs -f deploy/hermes -c onedrive`
   zeigt URL (`https://microsoft.com/devicelogin`) und Code, ca. 15 Minuten gültig. Mit dem M365-Konto
   anmelden, dessen OneDrive Hermes bekommen soll. Dasselbe mit `-c office` und `-c familie` (Konto muss die
   jeweilige Site sehen dürfen; kann dasselbe sein). Danach schreibt der Client `refresh_token` nach `conf/` und
   beginnt den ersten Abgleich (Fortschritt im Log). Läuft ein Code ab, beendet sich der Container, Kubernetes
   startet ihn neu, neuer Code im Log.
5. **`drive_id` später ändern:** neue ID eintragen, pushen, dann einmalig `ONEDRIVE_RESYNC` am betroffenen
   Sidecar im HelmRelease auf `"1"` setzen, Reconcile, nach dem Durchlauf wieder `"0"`. Alte Inhalte vorher aus
   `<name>/data` löschen (`kubectl … exec -c <name> -- sh -c 'rm -rf /onedrive/data/* /onedrive/data/.[!.]*'`),
   sonst lädt der Client sie in die neue Bibliothek hoch.
6. **Teilsynchronisation (je Sidecar):** Datei `m365/<name>.sync_list` anlegen (ein Pfad pro Zeile
   relativ zur Laufwerkswurzel, `!`-Zeilen schließen aus, `**` rekursiv; sobald die Datei existiert, ist alles
   andere ausgeschlossen), in `kustomization.yaml` als weiteres `files:`-Element eintragen, pushen, dann einmalig
   `ONEDRIVE_RESYNC=1` am betroffenen Sidecar wie in Schritt 5. Der Wrapper löscht eine alte `sync_list` aus
   `conf/`, wenn die ConfigMap keine mehr hat. **Stand 2026-11-18:** das OneDrive hat 751 GB, die ersten
   10 Gi der PVC waren nach einer Stunde voll. [`onedrive.sync_list`](../../infrastructure/karibik/hermes/m365/onedrive.sync_list)
   enthält deshalb nur `/Bachelorarbeit/`; `office` und `familie` laufen ohne Liste. Bereits geladene Daten
   außerhalb der Liste (ARCHIV, 08_Sonstiges, …) fasst der Client nach dem Resync nicht mehr an, sie belegen
   aber weiter Platz: nach dem Resync prüfen, dass das Log keine Löschvorgänge zeigt, dann die Ordner im
   Container löschen (`kubectl … exec -c onedrive -- rm -rf /onedrive/data/ARCHIV …`). Ausgeschlossene Pfade
   lösen keine Online-Löschung aus; Netz ist der Papierkorb (93 Tage).

**Prüfen:** `kubectl … logs deploy/hermes -c onedrive --tail=50` (bzw. `-c office`, `-c familie`: Monitor-Zyklen,
Up-/Downloads), dann im Chat „Was liegt unter /opt/onedrive und /opt/sharepoint?“ und als Schreibtest „Lege
unter /opt/sharepoint/familie/hermes-test.txt eine Datei mit dem heutigen Datum an“ – sie muss binnen Sekunden
in der Bibliothek erscheinen. Hänger: `hubble observe --from-pod hermes/ --verdict DROPPED`; taucht ein weiterer
Microsoft-Host auf, die Policy ergänzen. Exit-Code 78 eines Sidecars = Client verlangt `--resync` (Schritt 5).

**Trennen:** `<name>/conf/refresh_token` löschen (oder `ONEDRIVE_LOGOUT=1` einmalig am Sidecar) und in Entra
die App löschen. Volume-Verlust ist harmlos: Schritt 4 wiederholen, der Spiegel baut sich neu auf.

### Microsoft Graph (Skill `m365-graph`: OneDrive-Suche, Outlook-Mail, Kalender)

**Zweck:** Der Spiegel (voriger Abschnitt) deckt nur die gewählten Ordner ab. Für den Rest des OneDrive, für
Metadaten und Versionen und für Outlook gibt es das Skill
[`m365-graph`](../../infrastructure/karibik/hermes/m365-graph/SKILL.md) mit dem Skript
[`graph.py`](../../infrastructure/karibik/hermes/m365-graph/graph.py). Reines Python (MSAL für den Device-Code-
Flow und den Token-Cache, `requests` für Graph; beides ist in der Hermes-Umgebung vorhanden), JSON-Ausgabe,
kein MCP-Prozess. Befehle: `drive ls|search|info|get|versions` (nur lesend, `get` legt eine Lesekopie unter
`cache/m365-graph/` ab), `mail list|search|read|draft` (Entwurf, auch Antwortentwurf; nie senden),
`cal list|get|create|update|delete` (`delete` nur mit `--yes`). Laufwerke: `onedrive`, `office`, `familie` (die
Drive-IDs stehen im Skript) oder eine beliebige `b!…`-ID.

**Rechte und Grenzen:** zweite Entra-App `hermes-graph` (Public Client, Device-Code, dieselben drei Redirect-
URIs wie `hermes-onedrive`), delegiert `Files.Read.All`, `Sites.Read.All`, `Mail.ReadWrite`,
`Calendars.ReadWrite`, `User.Read`, `offline_access`. Kein `Mail.Send`: Entwürfe landen in Outlook, senden
muss der Nutzer. Keine Datei-Schreibrechte: Änderungen laufen über den Spiegel. `Mail.ReadWrite` erlaubt
technisch auch Verschieben und Löschen (30 Tage wiederherstellbar); das Skript bietet es nicht an, aber das
Token könnte es. Das Skill-Dokument und `HERMES.md` weisen den Agenten an, Mail- und Dateiinhalte als Daten
zu behandeln, nicht als Anweisungen.

**Deklarativ:** ConfigMap `m365-graph-skill` (Overlay, `configMapGenerator` aus `m365-graph/`), im HelmRelease
als Volume eingehängt; `install-extras` kopiert `SKILL.md` und `scripts/graph.py` bei jedem Start nach
`/opt/data/skills/productivity/m365-graph/` (Kopie statt Mount, damit das Verzeichnis UID 10000 gehört).
Token-Cache `/opt/data/m365_graph_token.json` (0600, Backup-TODO). Kein neuer Egress.

**Einrichtung:**

1. App anlegen (erledigt 2026-11-18 per Azure-CLI wie `hermes-onedrive`: `az ad app create` mit
   `--required-resource-accesses` aus den aufgelösten Scope-IDs, `az ad sp create`, `az ad app permission
   admin-consent` – auch hier schlug die Zustimmung direkt nach dem Anlegen einmal fehl, Wiederholung nach
   ~30 s ging durch). App `hermes-graph`, Anwendungs-ID `37e2fc1b-cfbf-4c14-8e17-b9e31bde2ee6`, Tenant wie oben,
   Grant `AllPrincipals` für alle sechs Scopes. Die App-ID steht im Skript (`CLIENT_ID`), die Tenant-ID ebenso.
2. Pushen, Reconcile, Pod-Neustart; Log von `install-extras` zeigt „Skill m365-graph … kopiert“.
3. Einmaliger Login (blockiert bis zur Bestätigung, maximal 15 Minuten):
   `kubectl --context karibik -n hermes exec -it deploy/hermes -c hermes-agent -- sh -c 'cd /opt/data && HOME=/opt/data /opt/data/bin/python skills/productivity/m365-graph/scripts/graph.py auth'`
4. Prüfen: `… graph.py me`, dann im Chat „Was steht diese Woche in meinem Outlook-Kalender?“ und „Suche in
   meinem OneDrive nach …“. Der Agent soll für gespiegelte Ordner weiterhin lokal arbeiten (Regel in
   `HERMES.md` und im Skill).

**Trennen:** Token-Datei löschen, in Entra die App `hermes-graph` löschen.

### Egress erweitern (Rezept)

Alle Policies liegen in [base/apps/hermes/networkPolicy.yaml](../../infrastructure/base/apps/hermes/networkPolicy.yaml),
Selektor `io.cilium.k8s.policy.serviceaccount: hermes`. Pro Freigabe eine eigene Policy, z. B.:

```yaml
# Skills-Hub / git clone von GitHub
- toFQDNs:
    - matchName: github.com
    - matchName: raw.githubusercontent.com
    - matchName: api.github.com
  toPorts:
    - ports:
        - port: "443"
```

„Internet prinzipiell ja“ (Web-Tools des Agenten), aber ohne Cluster-, VLAN- und Hetzner-Metadaten-Netze:

```yaml
- toCIDRSet:
    - cidr: 0.0.0.0/0
      except:
        - 10.0.0.0/8          # Hetzner-VLAN tortuga + Pod-/Service-Netze
        - 172.16.0.0/12
        - 192.168.0.0/16
        - 169.254.0.0/16      # Metadaten-Endpoint
  toPorts:
    - ports:
        - port: "443"
        - port: "80"
```

Diagnose bei Hängern immer zuerst: `hubble observe --from-pod hermes/ --verdict DROPPED` auf dem
Cilium-Pod des Nodes, auf dem Hermes läuft.

### Login später auf Keycloak-OIDC umstellen (nach dem Keycloak-Cutover)

Hermes unterstützt nur **public clients mit PKCE** (kein Client-Secret) und hat **keine User-Allowlist**:
jeder, dem Keycloak für den Client ein Token ausstellt, darf ins Dashboard → Zugriff in Keycloak
einschränken (eigener Realm oder Client-Rolle/Authorization).

1. Keycloak: Client `hermes-dashboard` (public, Standard Flow, PKCE S256), Redirect-URI
   `https://hermes.hummler.com/auth/callback`, Web Origin `https://hermes.hummler.com`.
2. HelmRelease: `dashboard.auth.provider: oidc` und
   `config.dashboard.oauth.self_hosted: { issuer: https://auth.<domain>/realms/hopps, client_id: hermes-dashboard }`;
   die drei `HERMES_DASHBOARD_BASIC_AUTH_*`-Keys aus `hermes-secrets` entfernen.
3. Einmalig `bootstrap.overwrite: true` mitgeben (config.yaml auf dem Volume wird sonst nicht
   überschrieben), nach dem Rollout wieder entfernen.
4. Egress-Policy für den Issuer ergänzen (`toFQDNs` auf den Keycloak-Host, Port 443 – geht über den LB
   zurück in den Cluster; alternativ `toEndpoints` auf die Keycloak-Pods, Port 8080, wenn der Issuer
   intern erreichbar sein soll).
5. Fehlerbild „`/auth/login` antwortet 503“ = Issuer-URL falsch (Discovery-Fehler steht im Body).

### Vorbehalte Codex-OAuth und Alternative `openai-api`

Die Codex-Variante wurde am 2026-11-08 bewusst gewählt, mit diesen bekannten Punkten:

- **Plan-Limits und Zulässigkeit**: Hermes dokumentiert nicht, wie die Nutzung auf die Codex-Limits des
  Plus-Abos angerechnet wird; ob OpenAI die Nutzung außerhalb der offiziellen Codex-Clients duldet, ist
  nicht belegt. Ein Sperr-Risiko für den ChatGPT-Account ist nicht auszuschließen.
- **Token-Familie**: Codex-Refresh-Tokens sind Einweg-Tokens mit Rotation. Zwei Programme mit *derselben*
  `auth.json` machen sich gegenseitig kaputt; laut Chart-Doku soll OpenAI bei einem zweiten Login desselben
  Accounts den älteren widerrufen (im Live-Test der Chart-Autoren trat das nicht auf). Fehlerbild: Hermes
  meldet „refresh token dead“ oder der Codex-CLI/Desktop verliert den Login. Abhilfe: einmalig
  `auth.deviceFlow.forceRelogin: true` setzen (oder `/opt/data/auth.json` löschen) und den Device-Login
  wiederholen.
- **Modelle**: nur Codex-Katalog des Accounts (z. B. `gpt-6-luna`, `-900k`-Alias für größeres Kontextfenster
  auf Kosten der Quota). Direkte API-Modelle sind nicht verfügbar.
- **Interaktiver Schritt**: jeder frische Login braucht einen Menschen (Device-Code). Nach einem
  Volume-Verlust (siehe Backup-TODO) also wieder Schritt 3.

**Zurück zu `openai-api` (Platform-Key, Pay-per-Token):** im HelmRelease `config.model.provider: openai-api`,
`default` z. B. `gpt-4o-mini`, `auth.deviceFlow.enabled: false`, `readyNotify.enabled: false`; in
`hermes-secrets` `OPENAI_API_KEY=sk-…` eintragen; in `networkPolicy.yaml` `api.openai.com` statt
`auth.openai.com`/`chatgpt.com` freigeben. Auf einem bestehenden Volume zusätzlich einmalig
`bootstrap.overwrite: true`, damit `config.yaml` neu geschrieben wird.

### ⚠️ TODO: Backup der Hermes-PVC

Die PVC `hermes` (Longhorn, 10Gi) ist die **einzige Kopie** von Memory, Skills, Sessions, `config.yaml`, der
OAuth-Tokens (`auth.json` Codex, `google_token.json`, `mcp-tokens/` Todoist und GitLab) und der nachinstallierten Python-Extras.
Zwei Longhorn-Replikate schützen vor Node-Ausfall, nicht vor Löschen oder kaputtem Volume. Noch offen:

1. Longhorn-Backup-Target auf karibik einrichten (S3 → DigitalOcean Space `hopps`, eigener Prefix
   `karibik-longhorn`; Credentials als Secret im Namespace `longhorn-system`, Werte in
   `defaultSettings.backupTarget`/`backupTargetCredentialSecret` des Longhorn-Overlays).
2. `RecurringJob` (z. B. täglich, Retention 14) mit Gruppe `default` oder Label-Selektor; PVC `hermes`
   per `recurring-job-group.longhorn.io/default: enabled` annotieren.
3. Restore-Test dokumentieren (Volume aus Backup → PVC → Pod neu starten).

Bis dahin gilt: Änderungen an `SOUL.md`/Skills sind nur auf dem Volume. Behelf:
`kubectl -n hermes exec deploy/hermes -c hermes-agent -- tar czf - /opt/data > hermes-data.tgz`.

### Stolpersteine

- **Helm wartet nicht auf den Menschen** (`install.disableWait: true`): Beim ersten Rollout am 2026-11-08 hat
  helm-controller nach 10 Minuten `ProgressDeadlineExceeded` des Deployments als „stalled“ gewertet, den Install
  abgebrochen und deinstalliert – der laufende Device-Login war damit weg. Deshalb wartet der Install nicht mehr
  (das HelmRelease ist `Ready`, bevor der Pod läuft; Pod-Status per `kubectl` prüfen), ein `postRenderers`-Patch
  setzt `progressDeadlineSeconds: 3600`, und die PVC trägt `helm.sh/resource-policy: keep`, damit kein
  Uninstall das Volume mitnimmt. Upgrades warten weiterhin, der Token liegt dann schon auf dem Volume.
- **Dashboard-Chat hängt in „queued“ / „retrying in 30s“, Telegram geht aber** (2026-11-08): Die TUI öffnet
  den Control-Socket `gateway.sock` nur, wenn `HERMES_HOME` dem Hermes-User (UID 10000) gehört
  (`unsafe_control_path`, `hermes_cli/gateway_runtime_discovery.py`). Die Wurzel eines Longhorn-Volumes gehört
  root, `fsGroup` setzt nur die Gruppe, chown ist unter PSS `restricted` unmöglich. Lösung im HelmRelease per
  `postRenderers`: `/opt/data` ist das Unterverzeichnis `home` des Volumes (`subPath`), ein vorgeschalteter
  Init-Container `prepare-home` (busybox, UID 10000) legt es an. Diagnose: `hermes chat -q test` im Pod zeigt
  den Fehler im Klartext; `/opt/data/logs/tui_gateway_crash.log` zeigt die Reconnect-Schleife. Die alten
  Dateien aus der Volume-Wurzel (erster Start) liegen noch dort, nur `auth.json` wurde nach `home/` kopiert.
- **Dashboard-Terminal hängt in „queued“ / „retrying in 30s“, Pod läuft seit Tagen** (2026-11-18): Variante
  des vorigen Falls mit anderem Grund: `gateway.sock` hatte Modus `0660` statt `0600`, der TUI-Bootstrap
  (`ui-tui/dist/scripts/gateway_bootstrap.py`) meldet `unsafe_control_permissions` und die TUI probiert es
  endlos alle 30 s. Verursacher war der fsGroup-Durchlauf des Kubelet (`fsGroupChangePolicy` Default
  `Always`): er setzt bei jedem Mount rekursiv `g+rw` auf alle Dateien, hier am 2026-11-13 05:50 UTC ohne
  Pod-Neustart (Kubelet-Neustart auf barbossa-kube), erkennbar an identischer ctime aller Dateien unter
  `/opt/data`. Sofort-Hilfe: `kubectl -n hermes exec deploy/hermes -c hermes-agent -- chmod 600
  /opt/data/gateway.sock`, die offene TUI verbindet beim nächsten Versuch. Dauerhaft: `fsGroupChangePolicy:
  OnRootMismatch` im `podSecurityContext` des HelmRelease (das Chart reicht den Wert durch). Diagnose
  reproduzierbar im Pod: `cd /opt/hermes && PYTHONPATH=/opt/hermes HERMES_PYTHON_SRC_ROOT=/opt/hermes
  /opt/hermes/.venv/bin/python ui-tui/dist/scripts/gateway_bootstrap.py --start` zeigt den Grund im Klartext.
- **Image 2,2 GB**: erster Pull dauert, dazu der Skills-Sync beim ersten Start. HelmRelease- und
  Kustomization-Timeout stehen deshalb auf 15 Minuten.
- **Dashboard „fails closed“**: fehlen die `HERMES_DASHBOARD_BASIC_AUTH_*`-Variablen, startet der
  Dashboard-Dienst nicht und der Ingress antwortet 502/503. Die Readiness-Probe (TCP 9119) bleibt rot.
- **config.yaml wird nur einmal geseedet** (`bootstrap.overwrite: false`): Änderungen an
  `dashboard.publicUrl`, `trustedProxies` oder dem Modell im HelmRelease greifen auf einem bestehenden
  Volume erst mit einmaligem `bootstrap.overwrite: true` (oder per Dashboard/`/model` zur Laufzeit).
- **Chart-Version ≠ Upstream-Version**: Chart 1.23.0 pinnt `v0.22.5`; Renovate liefert die Bumps.
- **Pod hängt in `Init:1/2`**: der Codex-Login wartet auf deine Bestätigung (Schritt 3). Kommt kein Telegram,
  Code aus `kubectl -n hermes logs deploy/hermes -c auth-device-login` nehmen; Ursache meist fehlendes `/start`
  an den Bot oder falsche `TELEGRAM_HOME_CHANNEL`. Egress-Drops gegen `auth.openai.com` per Hubble prüfen.
- **Namen**: Deployment, Service, ServiceAccount und PVC heißen alle `hermes` (`fullnameOverride`),
  die Container heißen `seed-config`, `auth-device-login` (Init) und `hermes-agent`, das SOPS-Secret
  `hermes-secrets`, das Chart-Secret `hermes-env`.

## 4f. Renovate (self-hosted) – Abhängigkeits-Updates für GitHub und GitLab

Renovate läuft als **CronJob auf karibik** und ersetzt die Mend-Renovate-App auf GitHub. Zwei Instanzen
aus demselben Chart, weil Renovate pro Lauf nur eine Plattform bedient:

| Instanz (HelmRelease) | Plattform | Repos | Zeitplan |
| --- | --- | --- | --- |
| `renovate-github` | github.com | `manuelhummler/flux-multicluster` | stündlich um :05 |
| `renovate-gitlab` | gitlab.com | `hummler-gbr/pro-makler` | stündlich um :35 |

### Entscheidungen (2026-11-11)

| Thema | Entscheidung | Begründung |
| ----- | ------------ | ---------- |
| Chart | Offizielles Chart [renovatebot/helm-charts](https://github.com/renovatebot/helm-charts) `46.422.2` als OCI (`oci://ghcr.io/renovatebot/charts`), Image `ghcr.io/renovatebot/renovate` = `appVersion` (`44.227.4`) | Upstream-Chart, schlank (CronJob + ConfigMap). Renovate bumpt sich selbst; weil das Chart mehrmals täglich erscheint, begrenzt eine `packageRule` in `renovate.json` die Bumps auf Montag früh. |
| Betriebsmodell | CronJob, **kein** Webhook/Ingress | Kein öffentlicher Endpunkt nötig. Nachteil: Reaktion auf Checkboxen im Dependency-Dashboard/MR erst beim nächsten Lauf (≤ 1 h). |
| Repos | **feste Liste** (`repositories`), kein `autodiscover` | Nichts passiert ungeplant, auch wenn das Token mehr Repos sehen kann. Neues Repo = Eintrag im HelmRelease. |
| Konfiguration | Global (Admin) im HelmRelease als `config.js`, Repo-Regeln bleiben im jeweiligen Repo (`renovate.json`) | `config.js` liest Tokens aus Umgebungsvariablen – nichts Geheimes in der ConfigMap. `requireConfig: required` + `onboarding: true`: ohne Repo-Config gibt es nur einen Onboarding-PR/-MR. |
| Secrets | Je Instanz eine SOPS-Datei im Overlay → Secret → `envFrom` | Muster wie hermes. Kein Registry-Token nötig (keine privaten Images im Repo). |
| Commits (GitHub) | `platformCommit: enabled` | Commits über die API, von GitHub als „Verified“ signiert, Autor ist der Token-Besitzer. |
| Egress | Entity `world` auf TCP 443, sonst default-deny | Renovate fragt jede Registry, jedes Helm-Repo und jede Paketquelle der Repos ab und lädt Tools nach – eine FQDN-Allowlist würde bei jeder neuen Abhängigkeit still brechen. `world` schließt kube-apiserver und Cluster-Pods aus. |
| Pod Security | Namespace `renovate` mit PSS `restricted`, UID 12021 (User des Images), kein SA-Token (postRenderer) | Root-FS bleibt beschreibbar: containerbase installiert node/helm/… zur Laufzeit. |
| Cache | keiner (kein PVC) | Jeder Lauf klont frisch; bei zwei kleinen Repos unkritisch. Bei Bedarf `renovate.persistence.cache` (Longhorn). |

### Aufbau

| Datei | Inhalt |
| --- | --- |
| [base/helm-repository/renovate.yaml](../../infrastructure/base/helm-repository/renovate.yaml) | OCI-HelmRepository (landet per Kustomize-`namespace` im Namespace `renovate`) |
| [base/apps/renovate/](../../infrastructure/base/apps/renovate/) | Namespace (PSS restricted), HelmReleases `renovate-github`/`renovate-gitlab`, CiliumNetworkPolicy, default-deny |
| [karibik/renovate/](../../infrastructure/karibik/renovate/) | Overlay + Secrets `renovate-github-env`/`renovate-gitlab-env` aus `*-encrypted.env` (SOPS); Vorlagen `*.env.example` |
| [clusters/karibik/infrastructure.yaml](../../clusters/karibik/infrastructure.yaml) | Kustomization `renovate` (keine Abhängigkeiten, Healthchecks auf beide HelmReleases) |
| [renovate.json](../../renovate.json) | neue `packageRule`: Bumps des Renovate-Charts nur montags vor 6 Uhr |

### Schritt 1 – Tokens anlegen

**GitHub** (`RENOVATE_TOKEN` der Instanz `renovate-github`): eigener **Bot-Account**, PRs und Commits
erscheinen unter diesem Account statt unter `manuelhummler`.

1. Mit deinem Account: `flux-multicluster` → Settings → Collaborators → Bot einladen, Rolle **Write**.
   Einladung mit dem Bot-Account annehmen (E-Mail oder github.com/notifications).
2. Mit dem Bot-Account: Settings → Developer settings → Personal access tokens → **Tokens (classic)** →
   Scopes **`repo`** und **`workflow`**, Ablaufdatum setzen und notieren.

Warum classic: Ein fine-grained PAT kann nur Repos seines eigenen Owners (User oder Organisation) auswählen.
Als Collaborator auf einem fremden **persönlichen** Repo taucht `flux-multicluster` dort nicht auf. Der classic
PAT gilt zwar für alle Repos des Bots, der hat aber nur dieses eine. `workflow` ist nötig, weil Renovate
`.github/workflows/` (MegaLinter-Action) aktualisiert, sonst lehnt GitHub den Push ab.
Lege das Repo später in eine Organisation, geht auch ein fine-grained PAT des Bots (Contents, Pull requests,
Issues, Workflows: Read and write; Commit statuses, Checks: Read).

**Kein GHCR-Token** (Entscheidung 2026-11-18): Die Mend-App hatte einen Lese-Zugang (Account `blackwolfk-9`)
für das private Image `ghcr.io/open-project-e-v/fest-der-demokratie`. Die App ist samt Registry-Regel und
Automerge-Regel aus dem Repo entfernt (siehe Phase 3), es gibt keine privaten Images mehr.

**GitLab** (`RENOVATE_TOKEN` der Instanz `renovate-gitlab`): Personal Access Token mit `api`, `read_user`,
`write_repository` eines Users, der in `hummler-gbr/pro-makler` mindestens *Developer* ist (Project/Group
Access Tokens gibt es auf gitlab.com nur in bezahlten Tiers). Ablaufdatum notieren – GitLab-PATs laufen
spätestens nach einem Jahr ab.

**GitHub lesend** (`GITHUB_COM_TOKEN` der GitLab-Instanz): Fine-grained PAT ohne Repo-Zugriff
(„Public repositories (read-only)“). Ohne Token gilt das anonyme Limit von 60 Requests/Stunde für Changelogs
und `github-releases`-Lookups; Feld leer lassen geht trotzdem.

### Schritt 2 – Secrets befüllen (vor dem Push)

Die verschlüsselten Dateien enthalten Platzhalter (`REPLACE_ME`):

```bash
sops infrastructure/karibik/renovate/renovate-github-env-encrypted.env
```

```bash
sops infrastructure/karibik/renovate/renovate-gitlab-env-encrypted.env
```

### Schritt 3 – Mend-App für flux-multicluster abschalten (vor dem ersten Lauf)

Sonst arbeiten zwei Renovates am selben Repo. GitHub → Settings → Applications → *Renovate* → Configure →
`flux-multicluster` aus der Repo-Auswahl entfernen (oder App deinstallieren).

Offene PRs und das Dependency-Dashboard-Issue der App gehören `renovate[bot]`. Der neue Renovate findet die
Branches gleichen Namens, sieht aber einen fremden Autor und fasst sie als „manuell bearbeitet“ nicht mehr an.
Daher: offene Renovate-PRs schließen (Branches löschen) und das alte Dashboard-Issue schließen – der erste Lauf
legt beides neu an.

### Schritt 4 – Rollout und erster Lauf

```bash
flux reconcile kustomization renovate --with-source
```

```bash
kubectl -n renovate get helmrelease,cronjob
```

Ersten Lauf nicht abwarten, sondern sofort aus dem CronJob starten und die Logs ansehen:

```bash
kubectl -n renovate create job --from=cronjob/renovate-github renovate-github-manual
```

```bash
kubectl -n renovate logs -f job/renovate-github-manual
```

Danach dasselbe mit `renovate-gitlab`. Erwartet: GitHub → neue PRs/Dashboard-Issue unter dem Token-Account;
GitLab → Onboarding-MR „Configure Renovate“ in `pro-makler` (enthält eine `renovate.json`, nach dem Merge
kommen die eigentlichen Update-MRs). Die manuellen Jobs danach löschen
(`kubectl -n renovate delete job renovate-github-manual`).

### Weiteres Repo hinzufügen

Repo in `repositories` des passenden HelmReleases ergänzen und dem Token Zugriff geben (fine-grained PAT:
Repository access erweitern). Ein Repo ohne Renovate-Config bekommt zuerst einen Onboarding-PR.

### Stolpersteine

- **Private Registry in pro-makler**: Nutzt das Repo eigene Images aus `registry.gitlab.com`, braucht Renovate
  dafür eine `hostRule` (z. B. in der `renovate.json` von pro-makler mit `{{ secrets.… }}` plus Eintrag unter
  `secrets` in der globalen Config der GitLab-Instanz). Symptom sonst: „Failed to look up docker package“.
- **Lookup-Fehler wegen Egress**: Nur 443 ist offen. Ein Helm-Repo über reines HTTP oder ein Git-Zugriff
  per SSH (Port 22) schlägt fehl – in Hubble als Drop im Namespace `renovate` sichtbar.
- **Token abgelaufen**: Job endet mit `Authentication failure` bzw. `401`; neuen Token per `sops` eintragen,
  pushen, Lauf manuell starten.
- **Job läuft länger als 50 Minuten**: `activeDeadlineSeconds: 3000` bricht ab, `concurrencyPolicy: Forbid`
  verhindert parallele Läufe derselben Instanz.

## 4g. Hindsight – Langzeit-Memory für Hermes (eingerichtet 2026-11-18)

[Hindsight](https://github.com/vectorize-io/hindsight) (Vectorize, MIT) ist ein Memory-System für Agenten:
`retain` zieht mit einem LLM Fakten, Entitäten und Zeitbezüge aus jedem Gesprächs-Turn, `recall` sucht
parallel über Vektoren, Stichwörter (BM25), Entitäts-Graph und Zeit und rerankt die Treffer, `reflect`
beantwortet Fragen mit Zitaten aus dem Gedächtnis. Hermes bindet es als **Memory-Provider-Plugin** an
(Auto-Recall vor jedem Turn, Auto-Retain danach, dazu die Tools `hindsight_recall`, `hindsight_retain`,
`hindsight_reflect`). Das eingebaute Hermes-Memory (`MEMORY.md`/`USER.md`) bleibt zusätzlich aktiv.
Die **Control-Plane-UI** unter `https://hindsight.hummler.com` zeigt Banks, Fakten und Entitäten und
erlaubt Testabfragen. Alles läuft im Cluster, nichts geht an Vectorize.

Vorher kurz ausprobiert und wieder verworfen: **Honcho** (Plugin am 2026-11-18 installiert, nie konfiguriert,
gleichentags entfernt). Honcho modelliert eine Person (Repräsentation, dialektisches Nachfragen) statt
Fakten und braucht entweder Honcho Cloud (Daten bei Plastic Labs) oder self-hosted einen bezahlten
OpenAI-API-Key für den Deriver; Hindsight kann das Abo per „Sign in with ChatGPT“ nutzen und bringt eine
offene UI mit.

### Entscheidungen (2026-11-18)

| Thema | Entscheidung | Begründung |
| ----- | ------------ | ---------- |
| Betrieb | **Self-hosted** im Namespace `hindsight`: offizielles Chart `oci://ghcr.io/vectorize-io/charts/hindsight` `1.2.1` (Images `ghcr.io/vectorize-io/hindsight-api` und `-control-plane`, Tag = appVersion), Modus `local_external` im Hermes-Plugin | Hindsight Cloud wäre bequemer (Dashboard, Credits), aber jeder Hermes-Turn und jeder Memory-Eintrag ginge an Vectorize. Kein eingebetteter Daemon im Hermes-Pod (`local_embedded` bräuchte dort Torch, Postgres und 2 GB RAM extra). |
| Datenbank | Rolle + Database `hindsight` im **CNPG-Cluster** (Phase 1), pgvector 0.8 ist im CNPG-Image; Chart-Postgres (`ankane/pgvector`) aus | Backup und Failover kommen gratis mit (Barman nach DigitalOcean). Haken: `vector` ist nicht „trusted“ – die Extension muss einmalig als Superuser angelegt werden, die `Database`-CRD des Operators 1.25 kennt `spec.extensions` noch nicht (ab 1.26). |
| LLM für retain/reflect | Provider `chatgpt` („Sign in with ChatGPT“, OAuth-Token des ChatGPT-Abos gegen `api.openai.com/v1/responses`), Modell `gpt-6-luna`, `HINDSIGHT_API_LLM_REASONING_EFFORT=low` | Kein API-Key (das Abo hat keinen API-Zugang). Vorbehalt wie bei Hermes (4e „Vorbehalte Codex-OAuth“): laut OpenAI „nur für persönliche Entwicklungsnutzung“, Preview, und das 5-Stunden-Limit teilt sich Hindsight mit Hermes/Codex. Ist es erschöpft, verschiebt Hindsight retain um 30 min (kein Datenverlust). `openai-codex` (dieselbe `auth.json` wie Hermes) **nicht** – zwei Prozesse auf einem Refresh-Token ergeben `refresh_token_reused`. |
| Embeddings / Reranker | lokal auf CPU, **mehrsprachig**: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dim) und `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | Die Chart-Defaults (`BAAI/bge-small-en-v1.5`, `hindsight-ai/hs-rerank-1`) sind englisch bzw. „acht Sprachen“ ohne Nennung von Deutsch. Die gewählten sind Hindsights eigenes Mehrsprachen-Beispiel (`docker/docker-compose/custom-models`). Download beim ersten Start von Hugging Face auf ein Cache-Volume. **Embedder später wechseln = `hindsight-admin reembed`** (Dimension allein reicht nicht). |
| Zugriff auf die Daten-API | Nur im Cluster: Cilium lässt an Port 8888 nur den Hermes-Pod und die eigene Control Plane; **zusätzlich Bearer-Key** (`HINDSIGHT_API_TENANT_EXTENSION` = `ApiKeyTenantExtension`, Key in SOPS), den Hermes (`HINDSIGHT_API_KEY`) und die Control Plane (`HINDSIGHT_CP_DATAPLANE_API_KEY`) mitschicken. Kein Ingress auf `/v1` | Die API hat von Haus aus keine Authentifizierung – wer sie erreicht, liest und löscht das ganze Gedächtnis. Zwei unabhängige Hürden statt einer Netzwerkregel. |
| UI-Login | `HINDSIGHT_CP_ACCESS_KEY` (Zufallswert in SOPS), Ingress Traefik + Let's Encrypt nur für die Control Plane | oauth2-proxy gibt es auf karibik (noch) nicht; nach dem Keycloak-Cutover kann ein ForwardAuth davor (die UI selbst kennt kein OIDC). |
| Storage | PVC `hindsight-api-model-cache` 5Gi, StorageClass `longhorn-cache` (1 Replik, keine Backup-Gruppe, kommt aus dem Hermes-Overlay), `helm.sh/resource-policy: keep`; trägt `/home/hindsight/.cache` (Modelle) und per subPath `/home/hindsight/.hindsight` (ChatGPT-Credentials) | Modelle sind reproduzierbar, der ChatGPT-Login in Minuten wiederholt – kein Backup nötig, aber `keep`, damit ein Helm-Uninstall den Login nicht mitnimmt. Die Memory-Daten selbst liegen in Postgres (gesichert). |
| Pod Security | Namespace `restricted`; Chart-Werte (non-root UID 1000, drop ALL, kein Privilege-Escalation) plus `seccompProfile: RuntimeDefault`; Rootfs beschreibbar; API ohne SA-Token | Beide Images schreiben Caches ins Rootfs (Next.js, Python). |
| Egress (API) | `postgres-cluster` (5432), Hugging Face (`huggingface.co`, `*.huggingface.co`, `hf.co`, `*.hf.co` bis drei Labels), OpenAI (`api.openai.com`, `auth.openai.com`, `chatgpt.com`), alle 443; Control Plane nur → API | Wie bei Hermes: eine Policy pro Ziel, Rezept 4e „Egress erweitern“. |
| Hermes-Seite | Init-Container `install-extras`: `hermes plugins install hindsight --enable --yes-deps` (nur wenn fehlend), schreibt `/opt/data/hindsight/config.json` (0600) bei jedem Start, entfernt ein etwaiges Honcho-Plugin; HelmRelease `config.memory.provider: hindsight`, `config.plugins.enabled: [hindsight]`, Secret `hermes-hindsight` als `HINDSIGHT_API_KEY` | Git bleibt die Wahrheit (config.yaml wird bei jedem Start neu geschrieben). Plugin-Updates bewusst von Hand (`hermes plugins update hindsight`), damit nicht jeder Pod-Start den Stand wechselt. Hermes hängt **nicht** per `dependsOn` an Hindsight: ist die API weg, puffert das Plugin retain und liefert keinen Recall – Hermes antwortet trotzdem. |
| Eine Bank | `bank_id: hermes` für alle Kanäle (Telegram, Dashboard, Cron) | Ein Nutzer, ein Gedächtnis. Mehrere Banks (z. B. pro Projekt) gingen über `bank_id_template` oder `.hindsight/config.toml` in vertrauten Repos. |

### Aufbau

| Datei | Inhalt |
| --- | --- |
| [base/helm-repository/hindsight.yaml](../../infrastructure/base/helm-repository/hindsight.yaml) | OCI-HelmRepository (per Kustomize-`namespace` im Namespace `hindsight`) |
| [base/apps/hindsight/](../../infrastructure/base/apps/hindsight/) | Namespace (PSS restricted), HelmRelease (`valuesFrom` für Passwort, API-Key, UI-Key), CiliumNetworkPolicies, default-deny + allow-acme |
| [karibik/hindsight/](../../infrastructure/karibik/hindsight/) | Overlay + Secrets `hindsight-db` (CNPG-Datei), `hindsight-api-key`, `hindsight-cp` (SOPS) |
| [karibik/cloudnative-pg-cluster/](../../infrastructure/karibik/cloudnative-pg-cluster/) | Rolle `hindsight` (cluster.yaml), Database `hindsight` (database.yaml), Secret aus `secrets/hindsight-encrypted.env` |
| [base/apps/hermes/helm-release.yaml](../../infrastructure/base/apps/hermes/helm-release.yaml) | `config.memory.provider`, `config.plugins.enabled`, `extraEnvFrom` `hermes-hindsight`, Plugin-Block in `install-extras` |
| [base/apps/hermes/networkPolicy.yaml](../../infrastructure/base/apps/hermes/networkPolicy.yaml) | `allow-egress-to-hindsight` (Namespace `hindsight`, Label `app.kubernetes.io/component: api`, 8888) |
| [karibik/hermes/kustomization.yaml](../../infrastructure/karibik/hermes/kustomization.yaml) | Secret `hermes-hindsight` aus `../hindsight/hindsight-api-key-encrypted.env` |
| `configs/secret-encrypted.env` | neuer Key `HINDSIGHT_DOMAIN=hindsight.hummler.com` |
| [clusters/karibik/infrastructure.yaml](../../clusters/karibik/infrastructure.yaml) | Kustomization `hindsight` (dependsOn longhorn, traefik, cluster-issuer, cloudnative-pg-cluster, hermes; Timeout 20m wegen 9-GB-Image) |

Die drei Zufallswerte (DB-Passwort, API-Key, UI-Key) wurden beim Einrichten erzeugt und mit dem
Cluster-Recipient verschlüsselt; nachsehen mit `sops -d <datei>`.

### Schritt 1 – cluster-secrets (erledigt 2026-11-18)

`HINDSIGHT_DOMAIN=hindsight.hummler.com` steht bereits in `configs/secret-encrypted.env` (per `sops set`
ergänzt, im selben Commit). Ohne den Key schlüge die Kustomization `hindsight` mit „variable not set“ fehl
(strict substitution). DNS ist schon da (`*.hummler.com` → karibik-LB).

### Schritt 2 – Push, pgvector anlegen, Rollout prüfen

1. Push. Flux legt zuerst Rolle und Database im CNPG-Cluster an (`cloudnative-pg-cluster`), dann Namespace,
   Secrets und HelmRelease `hindsight`.
2. **Einmalig** die Extension als Superuser anlegen (der DB-Owner darf das nicht, `vector` ist nicht „trusted“):

   ```bash
   kubectl -n cnpg-cluster exec postgres-cluster-1 -c postgres -- psql -U postgres -d hindsight -c 'CREATE EXTENSION IF NOT EXISTS vector'
   ```

   Vorher ist der API-Pod in `CrashLoopBackOff` (Migration scheitert) – das ist erwartet. Sobald der CNPG-Operator
   ≥ 1.26 ist (Renovate), kann das in `database.yaml` als `spec.extensions: [{name: vector}]` stehen.
3. Prüfen:

   ```bash
   flux -n flux-system get kustomizations hindsight hermes
   kubectl -n hindsight get pods,pvc,ingress
   kubectl -n hindsight logs deploy/hindsight-api --tail=50      # erster Start: Modell-Download (Minuten), dann "Uvicorn running"
   ```

   Der API-Pod meldet erst „ready“, wenn Embedder und Reranker geladen sind (Startup-Probe bis 15 min).

### Schritt 3 – ChatGPT-Login für retain/reflect (einmalig)

Ohne Login startet die API zwar, aber jeder `retain` schlägt fehl (Log: fehlende Credentials). Der Login läuft
über OpenAIs „Sign in with ChatGPT“; die Callback-URL zeigt auf `127.0.0.1:1455`, deshalb per Port-Forward
aus dem Pod heraus:

```bash
kubectl -n hindsight port-forward deploy/hindsight-api 1455:1455        # Terminal 1, offen lassen
kubectl -n hindsight exec -it deploy/hindsight-api -- hindsight-admin chatgpt login --no-browser   # Terminal 2
```

Die gedruckte URL im Browser auf dem Laptop öffnen, mit dem ChatGPT-Konto anmelden und die **Nutzung des Abos
in Drittanbieter-Apps** erlauben (Nachfrage im Flow; später erneut mit `--enable-plan-usage`). Der Browser ruft
danach `127.0.0.1:1455` auf dem Laptop auf, der Port-Forward trägt das in den Pod, das Skript schreibt
`/home/hindsight/.hindsight/chatgpt/credentials.json` (0600, auf dem Cache-Volume). Prüfen:

```bash
kubectl -n hindsight exec deploy/hindsight-api -- hindsight-admin chatgpt status
```

Alternative ohne Port-Forward: `pip install hindsight-api` auf dem Laptop, dort `hindsight-admin chatgpt login`,
die Datei in den Pod kopieren und `hindsight-admin chatgpt login --from-file <datei>` ausführen, danach auf
dem Laptop `hindsight-admin chatgpt logout --local-only` (das Refresh-Token rotiert, zwei Kopien kollidieren).

**Danach den API-Pod neu starten** – der laufende Prozess merkt sich „keine Credentials“ vom Start und liest die
Datei nicht nach (beobachtet 2026-11-18: retain scheiterte weiter, bis `kubectl -n hindsight rollout restart
deploy/hindsight-api`). Liegengebliebene Retain-Tasks wiederholt der Worker nur wenige Male; was in dieser Zeit
gesprochen wurde, landet nicht im Gedächtnis.

Das Token erneuert sich selbst (Access-Token 1 h). Nach einem Volume-Verlust: Schritt 3 wiederholen.

### Schritt 4 – Hermes und UI prüfen

```bash
kubectl -n hermes logs deploy/hermes -c install-extras | grep -i -E "hindsight|honcho"
kubectl -n hermes exec deploy/hermes -c hermes-agent -- sh -c 'cd /opt/data && hermes memory status'   # Provider: hindsight
kubectl -n hermes exec deploy/hermes -c hermes-agent -- sh -c 'cd /opt/data && hermes plugins list | grep -A1 hindsight'
```

Dann im Chat etwas Merkbares sagen („Merk dir: …“) und nach einem `/new` danach fragen. In der UI
(`https://hindsight.hummler.com`, Login mit dem UI-Key aus `sops -d infrastructure/karibik/hindsight/hindsight-cp-encrypted.env`)
erscheint die Bank `hermes` mit den extrahierten Fakten; dort lassen sich auch Recall-Abfragen testen.
Hermes markiert injizierte Erinnerungen im Chat mit 👁️ (`recall_indicator`).

### Betrieb

| Was | Wie |
| --- | --- |
| Chart-Update | Renovate bumpt `version` im HelmRelease; die Images hängen an der appVersion. API-Image ~9 GB: Rollout dauert. |
| Plugin-Update | `kubectl -n hermes exec deploy/hermes -c hermes-agent -- sh -c 'cd /opt/data && hermes plugins update hindsight'`, dann Pod-Neustart. Der Init-Container installiert nur, wenn das Verzeichnis fehlt. |
| Embedder wechseln | Env im HelmRelease ändern, Rollout, dann `kubectl -n hindsight exec deploy/hindsight-api -- hindsight-admin reembed`. Ohne reembed startet der Server bei anderer Dimension nicht. |
| Bank exportieren | `hindsight-admin export-bank` / `import-bank` im API-Pod (Migration, zweite Instanz). |
| Memory zurücksetzen | Bank in der UI löschen; die Hermes-Dateien `MEMORY.md`/`USER.md` sind davon unabhängig (`hermes memory reset`). |
| Keycloak-Cutover | Traefik-ForwardAuth vor den Ingress, `HINDSIGHT_CP_ACCESS_KEY` kann dann bleiben (zweiter Faktor) oder entfallen. |

### Stolpersteine

- **`401` in der UI oder im Hermes-Log** → API-Key-Mismatch: `hindsight-api-key` (Namespace `hindsight`) und
  `hermes-hindsight` (Namespace `hermes`) kommen aus derselben SOPS-Datei; nach einer Änderung beide Pods neu starten.
- **`refresh_token_reused`** im API-Log → die ChatGPT-Credentials wurden an zweiter Stelle benutzt (Laptop-Login
  nicht mit `logout --local-only` beendet). Schritt 3 wiederholen.
- **retain bleibt liegen** („deferred“, Retry in 30 min) → 5-Stunden-Limit des Abos erreicht, Hermes und Hindsight
  teilen es. `HINDSIGHT_API_LLM_REASONING_EFFORT=low` ist schon gesetzt; notfalls `auto_retain` im Plugin über
  `retain_every_n_turns` drosseln.
- **API startet nicht, „model init timeout“** → Hugging-Face-Download langsam; `HINDSIGHT_API_MODEL_INIT_TIMEOUT`
  (900 s) erhöhen oder Egress prüfen (`hubble observe -n hindsight --verdict DROPPED`).
- **Pod `Pending`, PVC `WaitForFirstConsumer`** → normal bis zum Scheduling; `longhorn-cache` hat 1 Replik, der
  Pod ist an den Node des Volumes gebunden.
- **Hermes: „Hindsight API … unreachable“** → Egress-Policy oder Service-Name; Test aus dem Hermes-Pod:
  `curl -s -o /dev/null -w '%{http_code}' http://hindsight-api.hindsight.svc.cluster.local:8888/health`.

## 5. Rezept: weitere App nach karibik holen (Phase 1-3)

Für jede App denselben Ablauf (vgl. [CLAUDE.md](../../CLAUDE.md) „Adding a New Application"):

1. `infrastructure/karibik/<app>/kustomization.yaml` → `resources: [../../base/apps/<app>]`
   (+ ggf. karibik-Patch / verschlüsselte Secrets).
2. Falls neue Werte nötig: Keys in `configs/secret-encrypted.env` ergänzen (neu verschlüsseln).
3. Block in `clusters/karibik/infrastructure.yaml` mit korrektem `dependsOn`, `healthChecks`,
   `decryption` (falls SOPS) und `postBuild.substituteFrom`.
4. Commit/Push → Flux reconciled → prüfen → **Daten migrieren** → **DNS umbiegen** → auf
   captain-kube abschalten.

## 6. Was GitOps NICHT macht

- **Daten**: Longhorn-PVs, Postgres-/MariaDB-Inhalte, MinIO-Buckets müssen aktiv kopiert
  werden (DB-Dumps, `mc mirror`, Longhorn-Backups). Flux legt nur Manifeste an, keine Daten.
- **DNS-Cutover**: App-Hostnames pro App vom captain-kube-Endpoint auf den karibik-LB
  (`142.132.246.190` / „port-royal") umziehen – das ist der eigentliche „Umzug".

## Weiterführende Dokumentation

- [Node-Setup (Hetzner)](node-setup-hetzner.md) – Node-Grundinstallation (endet bei §14 Flux-Operator).
- [Firewall-Konzept (Hetzner)](firewall-hetzner.md) – Host-Firewall pro Node.
- [configs/README.md](../../infrastructure/karibik/configs/README.md) – SOPS/cluster-secrets im Detail.
