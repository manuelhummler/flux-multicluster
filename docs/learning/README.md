# Lernpfad: Kubernetes-Cluster verstehen

Ziel: Alle Themen rund um den Betrieb des Karibik-Clusters nicht nur anwenden, sondern die
Prinzipien dahinter sicher erklären können. Der Pfad wächst Session für Session.

## Wie es abläuft

1. **Lektion** zu einem Thema – wo möglich mit Übungen direkt auf den Nodes, nicht nur Text.
2. **Praxistest** mit Verständnis- und Szenario-Fragen (Markdown-Datei, Antworten eintragen).
3. **Auswertung** – pro Frage: was stimmt, was fehlt, wo der Denkfehler sitzt.
4. Wissenslandkarte unten aktualisieren, Lücken werden zum nächsten Thema.

Ehrliches „weiß ich nicht" ist wertvoller als Raten – es zeigt genau, wo anzusetzen ist.

## Wissenslandkarte

Status: ✅ sicher · 🟡 teilweise (Intuition da, Details fehlen) · ❌ Lücke · ⬜ noch nicht begonnen

### Netzwerk
| Thema | Status | Notiz |
| --- | --- | --- |
| Public vs. private IPs, RFC-1918-Routing | ✅ | Praxistest 2026-10-10, Frage 1 |
| Hetzner-Netz: vSwitch, VLAN, Cloud-Network, LoadBalancer-Targets | 🟡 | Rollen verwechselbar (LB vs. direkter Pfad), Frage 2/3 |
| Verkehrsflüsse im Cluster (kubectl→LB→Node, Pod→Node, Node↔Node) | 🟡 | Frage 2/3 |
| Pod-/Service-Netz, Cilium VXLAN, Masquerading | 🟡 | nur angerissen |
| MTU / Path-MTU-Discovery | ⬜ | |

### Host-Firewall (netfilter / nftables)
| Thema | Status | Notiz |
| --- | --- | --- |
| Hooks: input / forward / output | ❌ | Frage 5 – **nächstes Thema** |
| Conntrack, stateful vs. stateless | ❌ | Frage 4 |
| Regelreihenfolge, Policy, Interface-basiertes Vertrauen | 🟡 | Frage 2 (falsche Regel genannt) |
| nftables vs. iptables-nft, Koexistenz mit Cilium, `flush ruleset` | ❌ | Frage 6 |
| Rate-Limiting mit dynamischen Sets | 🟡 | Frage 7 |

### SSH-Härtung
| Thema | Status | Notiz |
| --- | --- | --- |
| Key-only, PermitRootLogin, sudo | ✅ | umgesetzt |
| Drei Schichten: Rate-Limit → sshd → fail2ban (wer macht was, wann) | 🟡 | Frage 7 – Mechanik vertauscht |
| sshd `MaxAuthTries` vs. fail2ban | ❌ | Frage 7 |

### etcd
| Thema | Status | Notiz |
| --- | --- | --- |
| Client- vs. Peer-Verbindung | 🟡 | Frage 8 – „Client = apiserver" schärfen |
| Raft: Leader, Quorum, Heartbeats | 🟡 | Begriffe bekannt, Mechanik noch nicht geprüft |
| advertiseAddress → Peer-URL → SAN (Zusammenhang) | 🟡 | Frage 9 – welches Zertifikat, wo liegt die URL |
| Membership-State, `etcdctl member update`, listen vs. advertise | ❌ | Frage 9 |
| Migration auf private IPs (geplant) | ⬜ | nach 3. Node |

### Control-Plane & kubeadm
| Thema | Status | Notiz |
| --- | --- | --- |
| Komponenten: apiserver, scheduler, controller-manager, kubelet | ⬜ | |
| kubeadm Init/Join, Static Pods, Manifeste | ⬜ | |
| PKI / Zertifikate / SANs | 🟡 | Frage 9 |
| OIDC / RBAC | ⬜ | |
| HA, Quorum, 3. Node | 🟡 | Frage 10 |

### Cilium / CNI
| Thema | Status | Notiz |
| --- | --- | --- |
| kube-proxy-Replacement, Service-Routing | ⬜ | |
| NetworkPolicy / CiliumNetworkPolicy (Pod-Ebene) | ⬜ | |
| Hubble / Observability | ⬜ | |

### Weitere Themen (noch nicht begonnen)
CoreDNS · Ingress/Traefik · hccm & LoadBalancer-Provisionierung · Storage (Longhorn) ·
Flux/GitOps-Reconciliation · Helm · Prometheus/Monitoring · cert-manager · Upgrades/Backups

## Lernprotokoll

| Datum | Thema | Material | Ergebnis |
| --- | --- | --- | --- |
| 2026-10-10 | Firewall, Netzwerk, SSH, etcd | [Praxistest](2026-10-10-praxistest-firewall-etcd.md) | Netzwerk-Intuition solide; netfilter-Grundlagen = Hauptlücke |

## Nächster Schritt

**netfilter-Grundlagen mit Übungen auf gibbs:** Hooks (input/forward/output), Conntrack,
Regelreihenfolge, Koexistenz mit Cilium. Werkzeuge: `nft list table inet filter` (Trefferzähler),
`conntrack -L`, `journalctl -k | grep nft-drop-in`. Danach zweite Fragerunde nur zu Teil B.
