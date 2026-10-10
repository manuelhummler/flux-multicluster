# Praxistest: Firewall, Netzwerk & etcd (Karibik-Cluster)

Beantworte jede Frage in eigenen Worten unter **Antwort:**. Stichpunkte reichen.
Bist du unsicher, schreib es dazu – das ist für die Auswertung genauso wertvoll.

---

## Teil A – Netzwerk und IPs

### 1. Private IP ohne Firewall

Jeder Node hat zwei IPs. Erkläre, warum ein Paket von einem Scanner im Internet die Adresse
`10.0.1.3` niemals erreichen kann – auch ohne jede Firewall.

**Antwort:** Die Addresse 10.0.1.3 ist nur im internen privaten Netzwerk erreichbar und wird nicht via BGP zu unserem server geroutet. 

### 2. Weg eines Metrics-Pakets

Ein Prometheus-Pod auf gibbs scrapt die etcd-Metrics von barbossa unter `10.0.1.3:2381`.
Beschreibe grob den Weg des Pakets: über welche Interfaces läuft es, und welche Regel in der
nftables-Config von barbossa lässt es am Ende durch?

**Antwort:** es läuft erstmal über ein internes interface im pod, dann über den cluster egress via cilium auf das server interface von gibbs. hier wird es dann potentiell von ausgehenden nftables-configs abgehalten. danach läuft es über das hetzner fontäne der jugend gateway und findet im privaten netzwerk den server barbossa. hier läuft es dann potentiell in die iptables abwehr. diese regel hier lässt das paket aber zu, da das private netz ausgeklammert ist: iifname != $pub_if ip saddr { $priv_net, $pod_net, $svc_net } accept

### 3. Szenario: LB mit öffentlichen Targets

Der LB `fontaene-der-jugend` wäre nicht mit privaten, sondern mit öffentlichen Targets
konfiguriert. Was würde nach dem Firewall-Rollout passieren, wenn du `kubectl get nodes`
ausführst – und welche Regel in der Vorlage hätten wir dafür gebraucht?

**Antwort:** Hier würde die abstimmung ncihtmehr funktionieren, da die abstimmungspakete des etcds von der iptable regel abgelehnt werden würde, da diese über das public interface gesendet werdden würden, welches geblockt wird. entweder die erlaubnis von den expliziten source ips erlauben, oder das public interface generell zulassen.

---

## Teil B – nftables

### 4. Conntrack

Die Regel `ct state established,related accept` steht ganz oben. Erkläre, was sie tut und
warum ohne sie selbst ein simples `apt update` auf dem Node nicht funktionieren würde –
obwohl `output` auf `accept` steht.

**Antwort:**

keine Ahnung

### 5. forward vs. input

Warum haben wir `forward` auf `policy accept` gelassen, aber `input` auf `policy drop`?
Was würde kaputtgehen, wenn man auch `forward` auf `drop` setzt, ohne weitere Regeln?

**Antwort:** den unterschied kenne ich noch nicht, weder bei den keys noch bei den values

### 6. Szenario: `systemctl restart nftables`

Du führst auf gibbs versehentlich `sudo systemctl restart nftables` aus. Was passiert
technisch – und warum ist es schlimmer, als nur die eigene Tabelle zu verlieren?

**Antwort:** weiß ich nicht.

---

## Teil C – SSH-Absicherung

### 7. Bot auf Port 22

Ein Bot hämmert mit 50 Verbindungsversuchen pro Minute auf Port 22 von barbossa. Beschreibe,
was mit seinen Paketen an jeder der drei Schutzschichten passiert – und erkläre, warum du
dich währenddessen trotzdem problemlos einloggen kannst.

**Antwort:** iptables lässt port 22 standardmäßig auch von außerhalb zu. fail2ban macht als nächstes irgendwas, das habe ich aber noch nicht ganz verstanden. sshd legt einen eintrag an beim ersten verbindungsversuch und wenn dieser fehlerhaft ist, wird ein zähler hochgezählt. nach 5 Versuchen wird die verbindung standardmäßig wegen zu vielen wiederholungsversuchen abgelehnt, ohne den pubkey/password überhaupt zu prüfen.

---

## Teil D – etcd

### 8. Client vs. Peer

Erkläre den Unterschied zwischen Client- und Peer-Verbindung bei etcd. Welche der beiden
verlässt bei eurem Setup den Node überhaupt, und warum?

**Antwort:** eine client verbindung ist von außen, wenn man mit dem etcd kommunizieren möchte. in unserem setup verlässt nur die peer verbindung zur abstimmung die node. 

### 9. Szenario: nur die Peer-URL ändern

Du änderst auf barbossa im etcd-Manifest nur `--initial-advertise-peer-urls` auf
`https://10.0.1.3:2380` und startest etcd neu. Nenne zwei verschiedene Gründe, warum der
Member danach trotzdem nicht über die private IP mit gibbs kommuniziert.

**Antwort:** Das SSL Zertifikat auf gibbs wurde noch nicht angepasst und enhält die SAN mit der neuen privaten ip noch nicht. Die IPTables blockieren den port potentiell noch im internen netzwerk.

### 10. Der 3. Node und `node_pub_ips`

Der 3. Node soll mit `advertiseAddress: 10.0.x.x` joinen. Warum muss er danach *nicht* in
die Liste `node_pub_ips` der Firewall-Vorlage aufgenommen werden – und warum müssen barbossa
und gibbs trotzdem noch drin bleiben?

**Antwort:** weil er über das private netzwerk schon zugriff bekommt, ohne, dass er über das public interface geschleußt werden muss

---


# Auswertung (2026-10-10)

Legende: ✅ richtig · 🟡 teilweise · ❌ falsch / keine Antwort

## Teil A – Netzwerk und IPs

### 1 – ✅
`10.0.0.0/8` ist per RFC 1918 privater Adressraum. Kein Internet-Router hat eine Route dorthin;
Provider announcen und akzeptieren diese Präfixe nicht. Hetzner routet `10.0.0.0/16` nur
innerhalb des Projekts.

### 2 – 🟡
Richtung richtig. Zwei Fehler: (a) Der LB ist **nicht** beteiligt – Pod→Node-Traffic läuft direkt
zwischen den privaten IPs: Cilium masqueradet die Pod-Quelladresse auf die Node-IP (`10.0.0.2`),
schickt über `enp7s0`, Hetzners interner Router bringt es ins VLAN, Ankunft auf `eno1.4000`.
(b) Es greift Regel 2 `iifname "eno1.4000" accept`, nicht Regel 4 – erste passende Regel gewinnt.
`output`/`forward` auf gibbs blockieren nichts (Policy `accept`).

### 3 – ❌ (Thema verwechselt: etcd statt kubectl→LB→apiserver)
Mit öffentlichen Targets leitet der LB an `116.202.230.235:6443` weiter; Quell-IP ist der LB, keine
Regel erlaubt das → `drop`. `kubectl` läuft in den Timeout; kubelet und Cilium erreichen den
apiserver ebenfalls über diesen LB → Nodes werden `NotReady`. Nötige Regel (auskommentiert in der
Vorlage): `iifname $pub_if ip saddr $cp_lb_ip tcp dport 6443 accept`. „Public-Interface generell
zulassen" wäre keine Lösung – immer die engste Regel wählen.

## Teil B – nftables

### 4 – ❌
Eine Firewall sieht Pakete, keine Verbindungen. `apt update`: Node sendet an Port 443 (`output`
accept). Die **Antwort** kommt auf `eno1` an → `input`, Policy `drop`, keine Regel für eingehend
443 → verworfen, Verbindung kommt nie zustande. Conntrack merkt sich jede vom Host aufgebaute
Verbindung; `ct state established,related accept` lässt alle Pakete durch, die zu einer bekannten
Verbindung gehören. Das ist „stateful". `related` = Sonderfälle wie ICMP-Fehler zu einer
Verbindung.

### 5 – ❌
`input`/`forward`/`output` sind keine Keys, sondern die drei Stellen im Kernel, an denen ein
Paket vorbeikommt – je nach Ziel:
- `input`: Ziel ist eine IP dieses Hosts (SSH, kubelet, etcd, Metrics)
- `output`: der Host sendet selbst
- `forward`: das Paket geht **durch** den Host zu jemand anderem – zu den Pods (eigene IPs
  `172.22.x.x`)

`forward` auf `drop` ohne Regeln killt das Pod-Netzwerk: Pod-Egress, hostPort (Traefik),
Pod↔Pod über Nodes (nach VXLAN-Entpacken wird an den Pod weitergeleitet). Deshalb `accept`,
Pod-Ebene gehört Cilium.

### 6 – ❌
Ubuntu-Unit: `ExecStop = nft flush ruleset` → löscht **alle** Tabellen, auch die von Cilium und
portmap (per iptables-nft angelegt: `ip filter`, `ip nat`, …). Der Start lädt nur unsere Datei
→ unsere Tabelle da, Ciliums Masquerading/hostPort-DNAT weg, bis der Agent sie neu schreibt.
Schlimmer als eigene Tabelle verlieren: unsere Tabelle **schützt** nur, Ciliums Tabellen lassen
das Cluster **funktionieren**. Nur `nft -f` oder `systemctl reload`.

## Teil C – SSH

### 7 – 🟡
Richtig: Port 22 von überall erlaubt. Die drei Schichten korrekt:
1. nftables-Rate-Limit (Kernel, vor sshd): pro Quell-IP, Burst 10, danach 6/min – die übrigen
   44 SYN/min werden verworfen, sshd sieht sie nie.
2. sshd Key-only: die 6 bekommen sofort `Permission denied (publickey)`, Fehlversuch landet im
   Journal.
3. fail2ban (Userspace, **nach** dem Fehlversuch): liest Journal, 5 Fehler in 10 min → IP in
   eigene Tabelle `f2b-table`, 1 h alles von dieser IP verworfen.

Verwechslung: sshd `MaxAuthTries` (Standard 6) gilt **innerhalb einer Verbindung**; über
Verbindungen hinweg zählt nur fail2ban. fail2ban prüft keine Keys, es liest Logs und sperrt IPs.
Login weiterhin möglich, weil alle Schichten pro Quell-IP arbeiten und ein gültiger Key keinen
Fehlversuch erzeugt.

## Teil D – etcd

### 8 – 🟡
„Client" = wer Daten liest/schreibt, nicht „von außen" – in Kubernetes genau der kube-apiserver.
Client-Verbindung verlässt den Node nicht, **weil** jeder apiserver mit
`--etcd-servers=https://127.0.0.1:2379` nur seinen lokalen etcd anspricht (stacked etcd). Nur die
Peer-Verbindung (Raft) verlässt den Node.

### 9 – 🟡
Zertifikat: richtig, aber auf **barbossa** – gibbs ruft `10.0.1.3` an, barbossa zeigt sein
`peer.crt` vor, gibbs prüft die SAN. Firewall: falsch, privates Interface akzeptiert alles.
Die zwei gesuchten Gründe: (a) `--initial-advertise-peer-urls` wird nur beim ersten Start
gelesen; danach steht die URL im Clusterstatus → nur per `etcdctl member update` änderbar.
(b) `--listen-peer-urls` lauscht weiterhin nur auf `116.202.230.235` – auf `10.0.1.3:2380`
wartet niemand.

### 10 – 🟡 (inkl. Korrektur einer früheren Chat-Aussage)
Richtig: auf dem 3. Node selbst keine Regel 8 nötig (Peers erreichen ihn privat). Fehlend: Der
3. Node ruft barbossa/gibbs unter **deren** Peer-URLs (Public-IPs) an → über seine Default-Route
mit seiner Public-IP als Absender → auf `eno1` von barbossa greift Regel 8 → `drop`, solange seine
Public-IP nicht in `node_pub_ips` steht. Also **muss** sie dort rein (auf barbossa und gibbs),
bis die beiden auf private Advertise-Adressen migriert sind. Doku-Checkliste war schon richtig.

## Gesamtbild

| Bereich | Stand |
| --- | --- |
| Public/private IPs, Routing | solide |
| Verkehrsflüsse (LB vs. direkt) | Grundidee da, Rollen verwechselbar |
| netfilter: Hooks, Conntrack, nftables vs. iptables | **Hauptlücke** – 0 von 3 |
| SSH-Schichten | Prinzip klar, Mechanik vertauscht |
| etcd | Intuition gut, Details unsicher |

Nächster Schritt: siehe [README](README.md).
