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
| 3 | Apps: vaultwarden, kimai, dtrack, apicurio, fest-der-demokratie, minecraft, hopps-* (juno-care am 2026-11-08 ersatzlos entfernt, kein Umzug) | offen |
| neu | **Hermes Agent** (KI-Harness) unter `hermes.hummler.com`: keine Migration, Neuinstallation nur auf karibik (siehe 4e) | **läuft seit 2026-11-08**; offen: **Backup der PVC**, Egress erweitern, OIDC |
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
| Egress | **minimal**: `auth.openai.com`, `chatgpt.com` (Codex-Login/-Refresh und Inference), `api.telegram.org`, dazu für die lokale Transkription `pypi.org`, `files.pythonhosted.org`, `huggingface.co`, `*.huggingface.co`, `hf.co`, `*.hf.co` bis drei Labels tief, für Google Workspace `accounts.google.com`, `oauth2.googleapis.com`, `www.googleapis.com`, `*.googleapis.com`, für Todoist `ai.todoist.net`, `todoist.com` (alle 443, toFQDNs) | Erweiterung Schritt für Schritt (Rezept unten). Web-Tools des Agenten funktionieren erst, wenn Internet freigegeben ist. |
| Sprachnachrichten | `stt.provider: local` (faster-whisper, Modell `small`, Sprache automatisch) | Self-hosted statt Cloud-STT (Groq/OpenAI bräuchten eigenen Key, OpenAI-STT läuft nicht über den Codex-Login). Node hat 16 Kerne/64 GB, Pod-Limit 2 Gi reicht für `small` (int8). |
| config.yaml | `bootstrap.overwrite: true` – das Chart schreibt `config.yaml` bei jedem Start aus dem HelmRelease neu | Git ist die Wahrheit; Laufzeit-Änderungen (z. B. `/model` im Chat) überleben keinen Pod-Neustart. Dauerhafte Änderungen gehören ins HelmRelease. |
| Python-Extras | Init-Container `install-extras` (Kopie von seed-config per postRenderer) führt bei jedem Start `hermes pm install --extra google --extra stt-whisper` aus und legt `/opt/data/bin/python` an | Deklarativ statt Handarbeit im Pod; nach Volume-Verlust baut es sich selbst wieder auf. Der Wrapper gibt Skill-Skripten in der Agenten-Shell das Hermes-Python (sonst System-Python ohne Bibliotheken). |
| Todoist | Offizieller Remote-MCP-Server `https://ai.todoist.net/mcp` aus dem Hermes-Katalog, deklarativ als `config.mcp_servers.todoist` im HelmRelease; OAuth 2.1 mit Dynamic Client Registration, Einwilligung einmalig über das Dashboard, Token auf dem Volume | Entscheidung 2026-11-11. Kein API-Token in Git/SOPS nötig, kein lokaler MCP-Prozess im Pod (nur HTTP). Scope `data:read_write` (Aufgaben, Projekte, Kommentare lesen und schreiben). |
| Google Workspace | Skill `google-workspace` mit eigenem OAuth-Desktop-Client; Client-Secret als SOPS-Secret auf `/opt/data/google_client_secret.json` gemountet, Token `google_token.json` auf dem Volume | Entscheidung 2026-11-11. Volle Scopes (Gmail senden/ändern, Drive, Calendar, Sheets, Docs, Contacts) – Inhalte gehen beim Verarbeiten an OpenAI. Nur-Mail-Alternative wäre das `himalaya`-Skill mit App-Passwort. |
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
3. Das Dashboard meldet die gefundenen Tools. Danach eine **neue Session** beginnen (Telegram `/new` oder neuer
   Chat im Dashboard), damit die Tools geladen werden; hilft das nicht, `kubectl --context karibik -n hermes rollout
   restart deploy/hermes`.

Token-Dateien: `/opt/data/mcp-tokens/todoist.json` (Access/Refresh-Token, erneuert sich selbst),
`todoist.client.json` (der dynamisch registrierte Client), `todoist.meta.json`. Liegen nur auf dem Volume
(Backup-TODO); nach Volume-Verlust Schritt 1–3 wiederholen. Trennen: Dateien löschen oder in Todoist →
Einstellungen → Integrationen den Zugriff widerrufen.

**Prüfen:** `kubectl --context karibik -n hermes exec deploy/hermes -c hermes-agent -- sh -c 'HOME=/opt/data hermes mcp list'`
(zeigt den Server und den Auth-Status) und im Chat „Welche Aufgaben habe ich heute in Todoist?“.

**Fallback ohne Dashboard:** `kubectl … exec -it … -- sh -c 'HOME=/opt/data hermes mcp login todoist'` druckt die
Autorisierungs-URL; der Redirect auf `127.0.0.1:<port>` schlägt im Browser fehl, die komplette Adresszeile dann in
die Eingabeaufforderung einfügen (Paste-Fallback). Egress wie beim Dashboard-Weg.

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
OAuth-Tokens (`auth.json` Codex, `google_token.json`, `mcp-tokens/` Todoist) und der nachinstallierten Python-Extras.
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
