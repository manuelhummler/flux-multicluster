# Cluster- & Node-Übersicht

Dokumentation der Kubernetes-Cluster und der zugrunde liegenden Hetzner-Infrastruktur.

## Cluster

| Cluster        | Beschreibung                                                                 | Status                          |
| -------------- | --------------------------------------------------------------------------- | ------------------------------- |
| `captain-kube` | Ursprünglich Single-Node. Wird umgezogen, plattgemacht und neu aufgesetzt.   | Migration geplant / Neuaufsetzung |
| Hetzner 3-Node | Neues Cluster aus 3 Nodes (Root-Server + Cloud-Server).                      | Im Aufbau                       |

> **Hinweis:** Die `gotk-*.yaml`-Dateien unter `clusters/*/flux-system/` werden von Flux verwaltet und nicht manuell editiert.

## Nodes (Hetzner 3-Node-Cluster)

| Node           | Typ                | Öffentliche IP    | Private IP        | Rolle                       |
| -------------- | ------------------ | ----------------- | ----------------- | --------------------------- |
| `barbossa-kube`| Root-Server (groß) | `116.202.230.235` | `10.0.1.3/24` (VLAN) | Control-Plane + Worker (init) |
| `gibbs-kube`   | Cloud-Server       | `88.99.225.74`    | `10.0.0.2`        | Control-Plane + Worker (joined) |
| _(3. Node)_    | –                  | –                 | –                 | _geplant (für etcd-Quorum)_ |

> Beide Nodes sind kombinierte Control-Plane-/Worker-Nodes (Taint entfernt, siehe Setup
> Schritt 11). Für echtes HA-etcd-Quorum wird ein **dritter** Control-Plane-Node empfohlen.

## Hetzner-Netzwerk

| Ressource | Name          | Zweck                                    |
| --------- | ------------- | ---------------------------------------- |
| vSwitch      | `black-pearl` | Layer-2-Verbindung der Root-Server       |
| VLAN         | `tortuga`     | VLAN ID `4000`, Subnetz `10.0.0.0/16`, Gateway `10.0.1.1` |
| LoadBalancer | `fontaene-der-jugend`   | Public IP `142.132.246.190`. Balanced Control-Plane-Traffic (Port `6443`) auf `barbossa-kube` + `gibbs-kube` |

- Kein IPv6 konfiguriert (einige Anwendungen unterstützen es nicht).
- MTU im VLAN: `1400`.

### Kubernetes-Netzwerk

Bewusst außerhalb des Hetzner-Netzes (`10.0.0.0/16`) gewählt, um Kollisionen zu vermeiden:

| Zweck                    | Wert                |
| ------------------------ | ------------------- |
| Control-Plane-Endpoint   | `142.132.246.190:6443` (LoadBalancer `k8s-cp-lb`) |
| Service-Subnetz          | `172.20.0.0/16`     |
| Pod-Subnetz              | `172.22.0.0/16`     |
| DNS-Domain               | `cluster.local`     |

**OIDC-Auth** (kube-apiserver): Keycloak-Realm `alanda` unter
`https://keycloak.da-vinci.alanda.io/realms/alanda`, Client-ID `kube-apiserver`.

## Weiterführende Dokumentation

- [Node-Setup (Hetzner)](node-setup-hetzner.md) – Schritt-für-Schritt-Anleitung zum Aufsetzen eines Nodes.
