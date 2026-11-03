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
| 2 | Plattform/Auth: keycloak, oauth2-proxy, Monitoring-Stack (Grafana-OAuth) | offen |
| 3 | Apps: vaultwarden, kimai, dtrack, apicurio, juno-care, fest-der-demokratie, minecraft, hopps-* | offen |
| 4 | captain-kube leerräumen & als 3. Node rejoinen; **danach** Control-Plane auf private IPs (siehe 4c) | offen |

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

## 4c. Phase 4 – Control-Plane auf private IPs umstellen (erst nach dem 3. Node)

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
