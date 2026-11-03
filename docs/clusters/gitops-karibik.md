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
| 1 | Datenschicht: minio, Postgres (Konsolidierung Zalando ↔ CNPG), mariadb | offen |
| 2 | Plattform/Auth: keycloak, oauth2-proxy, Monitoring-Stack (Grafana-OAuth) | offen |
| 3 | Apps: vaultwarden, kimai, dtrack, apicurio, juno-care, fest-der-demokratie, minecraft, hopps-* | offen |
| 4 | captain-kube leerräumen & neu aufsetzen / als 3. Node rejoinen | offen |

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
