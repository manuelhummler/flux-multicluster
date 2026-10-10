# Firewall-Konzept (Hetzner) – Host-Firewall mit nftables

Absicherung der Karibik-Cluster-Nodes gegen Angriffe aus dem Internet und gegen Hetzner-
Abuse-Meldungen. Ziel: auf dem **öffentlichen** Interface jedes Nodes ist nur das Nötigste
erreichbar; der gesamte Kubernetes-/Metrics-Traffic bleibt auf dem privaten Netz.

> Werte, die pro Node angepasst werden müssen, sind mit ⚠️ markiert.
> Fertige Regeldatei: [`nftables/karibik-node.nft`](nftables/karibik-node.nft)

## Warum überhaupt?

Die Cluster-Komponenten exponieren bewusst Metrics-Endpunkte auf `0.0.0.0` (siehe
[node-setup-hetzner.md](node-setup-hetzner.md), Schritt 7):

- `etcd` Metrics auf `:2381`
- `kube-scheduler` / `kube-controller-manager` (`bind-address 0.0.0.0`) auf `:10259` / `:10257`
- `kubelet` auf `:10250`

Ohne Firewall antworten diese Ports auch auf der **öffentlichen** IP. Internet-Scanner finden
sie → **Hetzner-Abuse-Meldungen** und ein echtes Sicherheitsrisiko (ein öffentlich
erreichbares `etcd` wäre der Super-GAU). Die Firewall schließt diese Ports nach außen und lässt
sie intern offen.

## Entscheidung: nur Host-Firewall (nftables), keine Hetzner-Firewalls

Das Cluster mischt einen Root-Server (`barbossa`) und einen Cloud-Server (`gibbs`). Hetzner
bietet dafür zwei **getrennte** Firewall-Produkte, die beide hier nicht passen:

| Hetzner-Produkt     | gilt für      | Problem                                                                                   |
| ------------------- | ------------- | ----------------------------------------------------------------------------------------- |
| Cloud Firewall      | nur Cloud-Server | schützt `barbossa` **nicht**; filtert nur das Public-Interface, nie das private Netz    |
| Robot-Firewall      | nur Root-Server  | stateless, max. 10 Regeln, filtert auf Switch-Port-Ebene **inkl. vSwitch-/VLAN-Traffic** → kann Cluster-Traffic blocken |

Daher: **ein einheitlicher Mechanismus auf allen Nodes – nftables als Host-Firewall.**
Stateful, per Interface steuerbar, aktiv ab Boot (unabhängig von Cilium) und identisch für
Root- und Cloud-Server. Cloud Firewall und Robot-Firewall bleiben **deaktiviert / nicht
zugeordnet**, damit nicht doppelt gefiltert wird.

Pod-Level-Filterung (Pod↔Pod, Pod-Egress) ist **nicht** Aufgabe dieser Firewall – das macht
Cilium per `NetworkPolicy`/`CiliumNetworkPolicy`.

## Port-Referenz (wer muss was erreichen?)

Legende: 🌐 von außen erreichbar · 🔒 nur intern · ⛔ **darf nie öffentlich offen sein**.

| Port            | Proto   | Dienst                     | Scope  | Quelle                                             |
| --------------- | ------- | -------------------------- | ------ | -------------------------------------------------- |
| `22`            | TCP     | SSH                        | 🌐     | alle IPs, Rate-Limit pro Quell-IP + SSH-Härtung    |
| `6443`          | TCP     | kube-apiserver             | 🔒\*   | Admins via LB `fontaene-der-jugend`; Cluster-Peers |
| `2379`–`2380`   | TCP     | etcd (client + peer)       | ⛔\*   | nur Cluster-Peers                                  |
| `2381`          | TCP     | etcd Metrics               | ⛔     | Prometheus (in-cluster)                            |
| `10250`         | TCP     | kubelet API                | ⛔     | Control-Plane / Metrics (in-cluster, private IP)   |
| `10257`/`10259` | TCP     | controller-manager / scheduler Metrics | ⛔ | Prometheus (in-cluster)                     |
| `8472`          | UDP     | Cilium VXLAN               | 🔒     | Node↔Node (private IP)                             |
| `4240`          | TCP     | Cilium Health              | 🔒     | Node↔Node (private IP)                             |
| `80` / `443`    | TCP     | Ingress (Traefik)          | 🔒\*\* | Internet via LB `port-royal`                       |
| `30000`–`32767` | TCP/UDP | NodePort-Range             | 🔒     | LB via privates Netz                               |

\* **etcd/apiserver und Public-IPs:** Die kubeadm-Config setzt keine
`localAPIEndpoint.advertiseAddress`. kubeadm nimmt dann die IP der **Default-Route = Public-IP**
für `etcd --initial-advertise-peer-urls`/`--advertise-client-urls` und `kube-apiserver
--advertise-address`. Der etcd-Peer-Traffic und apiserver→etcd zwischen den Nodes laufen damit
sehr wahrscheinlich über die **Public-IPs**. Deshalb erlaubt das Regelwerk `2379`/`2380`/`6443`
auf dem Public-Interface **ausschließlich von den Public-IPs der anderen Cluster-Nodes**
(`node_pub_ips`). Der Traffic ist mTLS-verschlüsselt. → Prüfen, siehe Schritt 1.

\*\* **`80`/`443`:** Der Traefik-LB `port-royal` nutzt
`load-balancer.hetzner.cloud/use-private-ip: "true"`
([patch-traefik.yaml](../../infrastructure/karibik/traefik/patch-traefik.yaml)) und spricht die
Nodes übers **private Netz** an. Der Control-Plane-LB `fontaene-der-jugend` sollte ebenfalls
Private-Targets nutzen (⚠️ prüfen, Schritt 1). Dann muss auf der Node-Public-IP weder `80`/`443`
noch `6443` für die LBs offen sein.

## Das Regelwerk im Überblick

Datei: [`nftables/karibik-node.nft`](nftables/karibik-node.nft) → wird zu `/etc/nftables.conf`.

**Pro Node anzupassen (`define`-Block oben in der Datei):**

| Variable     | barbossa-kube         | gibbs-kube                      | Bedeutung                                  |
| ------------ | --------------------- | ------------------------------- | ------------------------------------------ |
| `pub_if`     | `eno1` ✅             | `eth0` ✅                       | öffentliches Interface (`ip -br a`)        |
| `priv_if`    | `eno1.4000` ✅        | `enp7s0` ✅                     | privates Interface (VLAN bzw. Cloud-Network) |
| `admin_ips`  | _(auskommentiert)_    | _(auskommentiert)_              | optional: feste Admin-IP/VPN statt „SSH von überall" |

**Cluster-weit gleich:** `priv_net 10.0.0.0/16`, `pod_net 172.22.0.0/16`, `svc_net
172.20.0.0/16`, `node_pub_ips { 116.202.230.235, 88.99.225.74 }` (⚠️ 3. Node ergänzen),
`cp_lb_ip 142.132.246.190`.

**Chain `input` (Policy `drop`), Reihenfolge:**

| # | Regel                                                   | Zweck                                                       |
| - | ------------------------------------------------------- | ----------------------------------------------------------- |
| 1 | `lo` accept; `established,related` accept; `invalid` drop | Basis-Stateful-Verhalten                                  |
| 2 | `iifname priv_if` accept                                | privates Hetzner-Netz voll vertrauen (etcd, kubelet, VXLAN, LB→Node, Metrics) |
| 3 | `cilium_host`/`cilium_net`/`cilium_vxlan`/`lxc*` accept | Pod→Host-Traffic (z. B. Prometheus scrapt `:2381`, `:10250`) |
| 4 | `!pub_if` + saddr aus `priv_net`/`pod_net`/`svc_net` accept | Catch-all für Cluster-CIDRs auf Nicht-Public-Interfaces |
| 5 | ICMP / ICMPv6 accept                                    | Ping, **Path-MTU-Discovery** (MTU 1400 im VLAN!), IPv6 Neighbor Discovery |
| 6 | DHCP-Antworten (`67→68`) auf `pub_if`                   | Hetzner Cloud vergibt die Public-IP per DHCP                |
| 7 | `pub_if` → `tcp 22` accept, max. 6 neue Verbindungen/min **pro Quell-IP** (dynamisches Set `ssh_ratelimit`) | SSH von überall (Admin wechselt Standort/IP); Brute-Force gebremst, ohne dass ein Scanner den Admin aussperren kann |
| 8 | `pub_if` + `node_pub_ips` → `tcp {2379,2380,6443}` accept | etcd-Peers / apiserver zwischen den Nodes (siehe oben)    |
| – | *(optional)* `pub_if` + `cp_lb_ip` → `tcp 6443`         | nur falls Control-Plane-LB Public-Targets nutzt             |
| 9 | `pub_if` → `log "nft-drop-in:"` (5/min)                 | geblockte Scans sichtbar in `journalctl -k`                 |
| – | **Policy `drop`**                                       | alles andere auf dem Public-Interface: zu                   |

### SSH-Härtung (Pflicht, da Port 22 weltweit offen ist)

Das Rate-Limit bremst nur. Der eigentliche Schutz ist, dass Passwort-Login gar nicht möglich
ist. Auf **jedem** Node (Admin-User `hummli`, siehe Node-Setup Schritt 6b):

```bash
# SSH-Key für hummli muss hinterlegt sein, bevor Passwort-Login abgeschaltet wird!
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
EOF
sudo sshd -t && sudo systemctl reload ssh
```

Zusätzlich `fail2ban`, das wiederholte Fehlversuche per nftables sperrt:

```bash
sudo apt install fail2ban
sudo tee /etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
banaction = nftables-multiport
banaction_allports = nftables-allports
bantime  = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
backend = systemd   # Ubuntu 24.04 hat kein /var/log/auth.log mehr
EOF
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

> fail2ban legt eine **eigene** nftables-Tabelle (`f2b-table`) an und kollidiert nicht mit
> `inet filter`. Vor dem Abschalten der Passwort-Auth in einer zweiten Shell testen, dass der
> Key-Login funktioniert.

**Chain `forward`: Policy `accept`** – bewusst, damit Cilium/Pod-Netz nicht gestört wird.
Hinweis: `hostPort`-Traffic (portmap-CNI, z. B. Traefik) läuft per DNAT durch `forward`, nicht
`input`. Traefik wäre damit auch direkt auf der Node-Public-IP erreichbar (kein
Sicherheitsproblem, es ist der Ingress). Wer das unterbinden will, aktiviert die auskommentierte
Regel `iifname $pub_if ct state new drop` in `forward` – erst nachdem alles läuft.

**IPv6:** Die Tabelle ist vom Typ `inet` und gilt damit für IPv4 **und** IPv6. Das ist
relevant, weil beide Nodes eine globale IPv6 auf dem Public-Interface haben (`barbossa`:
`2a01:4f8:241:4a54::2/64`, `gibbs`: `2a01:4f8:1c19:6e1f::1/64`), obwohl im Cluster kein IPv6
genutzt wird. IPv6-Inbound wird
genauso gedroppt; offen bleiben nur ICMPv6 (Neighbor Discovery) und bestehende Verbindungen.

## Rollout – Schritt für Schritt (pro Node)

> **Reihenfolge:** erst **einen** Node, Cluster-Health prüfen, dann den nächsten. Beide Nodes
> sind Control-Plane – ein Fehler auf beiden gleichzeitig kann das etcd-Quorum kosten.
> Rescue-Zugang bereithalten (Robot-Rescue für barbossa, Cloud-Console für gibbs).

### 1. Vorab-Checks (einmalig, auf einem Control-Plane-Node)

Welche IPs advertisen etcd und apiserver? (entscheidet, ob Regel 8 greifen muss)

```bash
kubectl -n kube-system get pod -l component=etcd \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{.spec.containers[0].command}{"\n\n"}{end}' \
  | grep -oE -- '--(initial-advertise-peer|advertise-client|listen-peer)-urls=[^" ]*'
```

- Zeigt `https://116.202.230.235:2380` / `https://88.99.225.74:2380` → **Public-IPs** → Regel 8
  ist zwingend (ist im Template aktiv). ✅
- Zeigt `https://10.0.1.3:2380` / `https://10.0.0.2:2380` → private IPs → Regel 8 wäre
  unnötig, schadet aber nicht.

Nutzen die LoadBalancer Private-Targets? Hetzner Cloud Console → **Load Balancers** →
`fontaene-der-jugend` → **Targets**: Nodes müssen über das **private Netz** angebunden sein
(„Use private IP"). Falls nicht: entweder umstellen (bevorzugt) oder die optionale `cp_lb_ip`-
Regel einkommentieren. `port-royal` wird vom hccm mit `use-private-ip: true` erzeugt.

Kein anderer Firewall-Mechanismus aktiv?

```bash
sudo ufw status            # muss "inactive" sein
sudo nft list ruleset | head -40   # zeigt nur Cilium-/portmap-Tabellen (ip filter/nat …), noch keine "inet filter"
```

### 2. Interfaces ermitteln (pro Node)

```bash
ip -br a
ip route show default      # Interface der Default-Route = pub_if
```

| Node            | `pub_if`       | `priv_if`          | private IP  |
| --------------- | -------------- | ------------------ | ----------- |
| `barbossa-kube` | `eno1` ✅ (statisch) | `eno1.4000` ✅   | `10.0.1.3`  |
| `gibbs-kube`    | `eth0` ✅ (DHCP)  | `enp7s0` ✅        | `10.0.0.2`  |

### 3. nftables installieren und Config einspielen

```bash
sudo apt install nftables
```

Vorlage [`nftables/karibik-node.nft`](nftables/karibik-node.nft) auf den Node kopieren (z. B.
per `scp`), den ⚠️-`define`-Block anpassen und nach `/etc/nftables.conf` legen:

```bash
sudo cp karibik-node.nft /etc/nftables.conf
sudo vim /etc/nftables.conf        # pub_if, priv_if, admin_ips anpassen
sudo nft -c -f /etc/nftables.conf  # Syntax-Check, keine Änderung
```

> Die von Ubuntu mitgelieferte `/etc/nftables.conf` beginnt mit `flush ruleset` – **nicht**
> übernehmen. Das würde die iptables-nft-Regeln von Cilium und portmap löschen.

### 4. Aktivieren mit Lockout-Schutz

Sicherheitsnetz: In 5 Minuten wird die Firewall-Tabelle automatisch wieder entfernt – es sei
denn, der Timer wird vorher gestoppt.

```bash
sudo systemd-run --on-active=5m --unit=nft-rollback nft delete table inet filter
sudo nft -f /etc/nftables.conf
```

Jetzt in einer **neuen** Shell testen, dass SSH noch geht (bestehende Session bleibt dank
`established` ohnehin offen). Funktioniert es:

```bash
sudo systemctl stop nft-rollback.timer   # Rollback abbrechen
sudo systemctl enable nftables           # beim Boot laden
```

Funktioniert SSH **nicht**: 5 Minuten warten, der Timer räumt auf – dann Config korrigieren.

> **Nie `systemctl stop|restart nftables`!** Der `ExecStop` des Ubuntu-Units macht
> `nft flush ruleset` und löscht damit auch Cilium-/portmap-Regeln. Änderungen nachladen immer
> mit `sudo nft -f /etc/nftables.conf` oder `sudo systemctl reload nftables`.

### 5. Cluster-Health prüfen (vor dem nächsten Node)

```bash
kubectl get nodes
kubectl get pods -A | grep -v Running
kubectl -n kube-system get pods -l component=etcd
kubectl -n cilium exec ds/cilium -- cilium-dbg status --brief
sudo journalctl -k --since -10m | grep nft-drop-in   # was wird geblockt?
```

Alle Nodes `Ready`, etcd-Pods `Running`, Cilium `OK` → weiter mit dem nächsten Node.

## Verifizierung von außen

Vom eigenen Rechner (nicht aus dem Cluster) – erwartet: nur `22` offen, alle ⛔-Ports
`filtered`:

```bash
nmap -Pn -p 22,80,443,2379,2380,2381,6443,10250,10257,10259 116.202.230.235 88.99.225.74
```

SSH-Härtung prüfen – Passwort-Login muss abgelehnt werden, noch bevor ein Passwort abgefragt
wird:

```bash
ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password hummli@116.202.230.235
# erwartet: "Permission denied (publickey)."
```

## Status / offene Punkte

- [x] SSH-Härtung auf allen Nodes: Key-only, kein Root-Login, passwortloses sudo – 2026-10-10
- [x] fail2ban auf allen Nodes aktiv (Journal-Backend), erste Scanner-IPs gebannt – 2026-10-10
- [x] Vorab-Check: etcd advertised **Public-IPs** (`116.202.230.235:2380`, `88.99.225.74:2380`) → Regel 8 zwingend – 2026-10-10
- [x] Vorab-Check: LB `fontaene-der-jugend` nutzt Private-Targets – gibbs als Cloud-Server-Target (`10.0.0.2`), barbossa als Dedicated-IP-Target (`10.0.1.3`); `ufw` auf beiden Nodes inactive – 2026-10-10
- [x] `gibbs-kube`: nftables aktiv (`eth0`/`enp7s0`), Cluster-Health ok (Nodes `Ready`, Cilium `OK`), SSH-Scanner laufen ins Rate-Limit – 2026-10-10
- [x] `barbossa-kube`: nftables aktiv (`eno1`/`eno1.4000`), Cluster-Health ok (Nodes `Ready`, beide etcd `Running`, Cilium `OK`) – 2026-10-10
- [x] `nmap`-Verifizierung von außen: beide Nodes nur `22` open, alle ⛔-Ports sowie `80`/`443`/`6443` `filtered` – 2026-10-10
- [ ] Beim 3. Node: `node_pub_ips` auf allen Nodes ergänzen + Firewall auf dem neuen Node
- [ ] Später (optional): `advertiseAddress` auf private IPs umstellen → Regel 8 entfällt
- [ ] Später (optional): SSH auf feste Admin-IP/VPN einschränken (`admin_ips` einkommentieren)
