# Node-Setup (Hetzner)

Schritt-für-Schritt-Anleitung zum Vorbereiten eines Nodes für das Hetzner-Kubernetes-Cluster.
Getestet mit Ubuntu, Kubernetes `v1.36`, containerd als Runtime.

Als konkretes Beispiel dient der Node **`barbossa-kube`** (Root-Server, öffentliche IP
`116.202.230.235`, VLAN-IP `10.0.1.3`).

> Werte, die pro Node angepasst werden müssen, sind mit ⚠️ markiert.

## 1. Kernel-Module & sysctl

Erforderliche Kernel-Module laden (`overlay`, `br_netfilter` für das Container-Networking,
`dm_crypt` für verschlüsselte Volumes):

```bash
cat <<EOF | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
dm_crypt
EOF
sudo modprobe overlay
sudo modprobe br_netfilter
```

sysctl-Parameter setzen. Es wird **kein IPv6** konfiguriert, da einige Anwendungen es nicht
unterstützen:

```bash
# No IPv6 configured as few applications don't support it
cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
# if ipv6 support is needed add also following parameter
#net.ipv6.conf.all.forwarding = 1
EOF
sudo sysctl --system
```

## 2. Basispakete installieren

```bash
sudo apt update
sudo apt install \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    nfs-common
```

## 3. containerd installieren

Docker-APT-Repository einbinden und containerd installieren:

```bash
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install containerd.io
```

containerd konfigurieren und den systemd-Cgroup-Treiber aktivieren (erforderlich für kubelet):

```bash
sudo mkdir -p /etc/containerd
sudo bash -c 'containerd config default > /etc/containerd/config.toml'
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
sudo systemctl enable containerd
sudo systemctl restart containerd
```

## 4. Kubernetes-Binaries installieren

Kubernetes-APT-Repository (`v1.36`) einbinden und `kubelet`, `kubeadm`, `kubectl`
installieren. Die Pakete werden auf `hold` gesetzt, damit sie nicht automatisch aktualisiert
werden:

```bash
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.36/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.36/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo apt-get update
sudo apt-get install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl
```

## 4b. multipathd entfernen

`multipathd` kann mit Block-Storage von Longhorn kollidieren (es beansprucht die Longhorn-
Devices). Daher wird der Dienst auf allen Nodes deaktiviert und `multipath-tools` entfernt:

```bash
sudo systemctl disable --now multipathd
sudo apt purge multipath-tools
```

## 5. VLAN-Netzwerk konfigurieren (netplan) — nur Root-Server

> **Nur für Root-Server** (z. B. `barbossa-kube`). Cloud-Server wie `gibbs-kube` hängen
> direkt am Hetzner-Cloud-Network und bekommen ihre private IP automatisch – bei ihnen
> entfällt dieser Schritt, es geht direkt weiter mit Schritt 6.

Damit die Root-Server über das private Hetzner-VLAN `tortuga` (via vSwitch `black-pearl`)
kommunizieren, wird ein VLAN-Interface auf der Basis-Netzwerkkarte angelegt.

`sudo vim /etc/netplan/01-netcfg.yaml` – folgenden Block ergänzen:

```yaml
  # VLAN configuration starts here
  vlans:
    # name here should contain base network interface
    eno1.4000:            # ⚠️ Basis-Interface (z. B. eno1) an den Node anpassen
      id: 4000            # VLAN ID des Hetzner-VLANs "tortuga"
      link: eno1          # ⚠️ Basis-Netzwerkkarte
      mtu: 1400           # MTU für Hetzner-vSwitch
      addresses:
        # this IP needs to be unique for all root servers in the same network
        - 10.0.1.3/24     # ⚠️ eindeutige VLAN-IP pro Root-Server (barbossa: 10.0.1.3)
      routes:
        - to: "10.0.0.0/16"
          via: "10.0.1.1"
```

Anschließend anwenden:

```bash
sudo netplan apply
```

> **Wichtig:** Die VLAN-IP (`addresses`) muss für jeden Root-Server im selben Netzwerk
> eindeutig sein. Gateway des VLANs ist `10.0.1.1`, das gesamte Netz ist `10.0.0.0/16`.

## 6. kubelet auf die VLAN-IP binden

Damit kubelet die private VLAN-IP als Node-IP nutzt (statt der öffentlichen), wird sie in
den kubelet-Extra-Args gesetzt.

`sudo vim /etc/default/kubelet`:

```bash
KUBELET_EXTRA_ARGS=--node-ip=10.0.1.3    # ⚠️ private IP dieses Nodes
```

Private IPs pro Node:

| Node            | Typ          | `--node-ip`   |
| --------------- | ------------ | ------------- |
| `barbossa-kube` | Root-Server  | `10.0.1.3`    |
| `gibbs-kube`    | Cloud-Server | `10.0.0.2`    |

## 6b. Admin-User anlegen (`hummli`)

Für die Administration wird ein dedizierter User `hummli` mit sudo-Rechten angelegt (auf
allen Nodes):

```bash
# User anlegen (inkl. Home-Verzeichnis und Passwort-Prompt)
sudo adduser hummli
# In die sudo-Gruppe aufnehmen -> volle root-Rechte via sudo
sudo usermod -aG sudo hummli
```

Optional: SSH-Key hinterlegen (Login ohne Passwort) und ggf. passwortloses sudo. Die
kubeconfig (Schritt 8) wird als dieser User eingerichtet.

## 7. kubeadm-Konfiguration (Control-Plane / Root-Server)

Auf dem ersten Control-Plane-Node (Root-Server `barbossa-kube`) wird die Cluster-
Konfiguration unter `~/config.yaml` abgelegt. Sie kombiniert `KubeletConfiguration` und
`ClusterConfiguration` und wird später von `kubeadm init` verwendet.

```yaml
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
# allow swap usage, by default no container will use it, can also be disabled if not needed (also in /etc/fstab)
failSwapOn: false
featureGates:
  NodeSwap: true
memorySwap:
  swapBehavior: LimitedSwap
# increase maxPods, to have enough space on singlenode/smaller systems
maxPods: 250
---
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
networking:
  dnsDomain: cluster.local
  # a service subnet needs to be choosen which doesn't collide with hetzner network
  serviceSubnet: "172.20.0.0/16"
  podSubnet: "172.22.0.0/16"
# needs to be only configured, when floating IP/LoadBalancer is used
controlPlaneEndpoint: "142.132.246.190:6443"    # ⚠️ Public IP des LoadBalancers k8s-cp-lb
# configure scheduler and controllerManager to expose metrics
scheduler:
  extraArgs:
    - name: bind-address
      value: "0.0.0.0"
controllerManager:
  extraArgs:
    - name: bind-address
      value: "0.0.0.0"
    #- name: cloud-provider
    #  value: external
etcd:
  local:
    extraArgs:
      # enable to scrape metrics from Prometheus
      # Attention: be aware to limit traffic to only from in cluster (see firewall rules)
      - name: listen-metrics-urls
        value: http://0.0.0.0:2381
apiServer:
  extraArgs:
    - name: oidc-client-id
      value: kube-apiserver
    - name: oidc-issuer-url
      value: https://keycloak.da-vinci.alanda.io/realms/alanda
    - name: oidc-username-claim
      value: email
    - name: oidc-groups-claim
      value: groups
```

### Wichtige Entscheidungen in dieser Config

- **Swap aktiviert** (`failSwapOn: false`, Feature-Gate `NodeSwap`, `swapBehavior: LimitedSwap`).
  Standardmäßig nutzt kein Container Swap; bei Bedarf auch in `/etc/fstab` deaktivierbar.
- **`maxPods: 250`** – mehr Pods pro Node für kleinere/Single-Node-Setups.
- **Netzwerk-Subnetze** – bewusst so gewählt, dass sie **nicht** mit dem Hetzner-Netz
  (`10.0.0.0/16`) kollidieren:
  - Service-Subnetz: `172.20.0.0/16`
  - Pod-Subnetz: `172.22.0.0/16`
- **`controlPlaneEndpoint: 142.132.246.190:6443`** – Public IP des Hetzner LoadBalancers
  `k8s-cp-lb`, der den Control-Plane-Traffic (Port `6443`) auf `barbossa-kube` und
  `gibbs-kube` verteilt (nur nötig, wenn Floating IP/LB verwendet wird).
- **Metrics exponiert** für Prometheus: `scheduler`, `controllerManager` (`bind-address 0.0.0.0`)
  und `etcd` (`listen-metrics-urls http://0.0.0.0:2381`).
  > ⚠️ Traffic auf diese Metrics-Endpunkte per Firewall auf cluster-internen Zugriff beschränken.
- **OIDC-Authentifizierung** über Keycloak:
  - Issuer: `https://keycloak.da-vinci.alanda.io/realms/alanda`
  - Client-ID: `kube-apiserver`, Username-Claim: `email`, Groups-Claim: `groups`

## 8. Control-Plane initialisieren (kubeadm init)

Auf dem ersten Control-Plane-Node (`barbossa-kube`) wird das Cluster mit der zuvor
angelegten Config initialisiert:

```bash
sudo kubeadm init --config=config.yaml --skip-phases=addon/kube-proxy
```

- **`--config=config.yaml`** – nutzt die kombinierte Kubelet-/Cluster-Config aus Schritt 7.
- **`--skip-phases=addon/kube-proxy`** – `kube-proxy` wird bewusst **nicht** installiert, da
  **Cilium** als CNI die kube-proxy-Funktionalität ersetzt (kube-proxy replacement).

Nach erfolgreichem `init`:

- kubeconfig für den Admin-User (`hummli`, siehe Schritt 6b) einrichten – als `hummli`
  ausgeführt:

  ```bash
  mkdir -p $HOME/.kube
  sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
  sudo chown $(id -u):$(id -g) $HOME/.kube/config
  ```

- Die von `kubeadm` ausgegebenen **`kubeadm join`-Befehle** (für Control-Plane- und
  Worker-Nodes) sichern – sie enthalten Token und CA-Cert-Hash und werden für `gibbs-kube`
  und den dritten Node benötigt.

> Bis eine CNI installiert ist, bleiben Node und CoreDNS-Pods `NotReady`/`Pending` – das ist
> zu diesem Zeitpunkt erwartet.

## 9. Helm installieren (Control-Plane / Root-Server)

Auf `barbossa-kube` wird Helm (v4) über das offizielle Installskript installiert – wird für
die Cilium-Installation (Schritt 10) benötigt:

```bash
curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-4
chmod 700 get_helm.sh
./get_helm.sh
```

## 10. Cilium als CNI installieren (Control-Plane / Root-Server)

Cilium wird als CNI und kube-proxy-Ersatz installiert (passend zu `--skip-phases=addon/kube-proxy`
aus Schritt 8). Auf `barbossa-kube`:

```bash
helm repo add cilium https://helm.cilium.io/
helm repo update
```

```bash
API_SERVER_IP=142.132.246.190
API_SERVER_PORT=6443
helm install cilium cilium/cilium \
  --set hubble.tls.enabled=false \
  --set kubeProxyReplacement=true \
  --set k8sServiceHost=${API_SERVER_IP} \
  --set k8sServicePort=${API_SERVER_PORT} \
  --set cni.chainingMode=portmap \
  --namespace cilium \
  --create-namespace \
  --set operator.replicas=1 \
  --set ipam.operator.clusterPoolIPv4PodCIDRList=172.22.0.0/16
```

### Wichtige Parameter

- **`kubeProxyReplacement=true`** – Cilium übernimmt die Aufgaben von kube-proxy (das in
  Schritt 8 übersprungen wurde).
- **`k8sServiceHost` / `k8sServicePort`** – da ohne kube-proxy kein `kubernetes`-Service-
  ClusterIP-Routing existiert, bevor Cilium läuft, wird der API-Server explizit über die
  LoadBalancer-IP `142.132.246.190:6443` (`k8s-cp-lb`) angegeben.
- **`ipam.operator.clusterPoolIPv4PodCIDRList=172.22.0.0/16`** – muss mit dem `podSubnet`
  aus der kubeadm-Config (Schritt 7) übereinstimmen.
- **`cni.chainingMode=portmap`** – ermöglicht `hostPort`-Mappings.
- **`operator.replicas=1`** – ein einzelner Operator (Cluster im Aufbau).
- **`hubble.tls.enabled=false`** – Hubble-TLS deaktiviert.

Nach der Installation sollten Cilium- und CoreDNS-Pods `Running` werden und der Node in den
Status `Ready` wechseln:

```bash
kubectl get pods -n cilium
kubectl get nodes
```

## 11. Nodes für Workloads freigeben (Taints & Labels)

Damit auch auf den Control-Plane-Nodes normale Workloads laufen können (kombinierte
Control-Plane-/Worker-Nodes), werden Taint und LB-Exclude-Label entfernt und die Nodes als
Worker gelabelt:

```bash
# Control-Plane-Taint entfernen -> Pods können auf diesen Nodes scheduled werden
kubectl taint nodes --all node-role.kubernetes.io/control-plane-

# exclude-from-external-load-balancers-Label entfernen -> Node nimmt an externen LBs teil
kubectl label node --all node.kubernetes.io/exclude-from-external-load-balancers-

# Nodes zusätzlich als Worker labeln
kubectl label node --all node-role.kubernetes.io/worker=worker
```

> Das nachgestellte `-` an `...control-plane-` bzw. `...load-balancers-` entfernt Taint/Label
> (kubectl-Syntax). Diese Befehle wirken über `--all` auf alle bestehenden Nodes; nach dem
> Join weiterer Nodes ggf. erneut ausführen.

## 12. Join-Credentials erzeugen (auf barbossa)

Um weitere Nodes hinzuzufügen, werden auf `barbossa-kube` die nötigen Credentials erzeugt.

Certificate-Key für **Control-Plane-Joins** hochladen (nur nötig, wenn weitere Control-Plane-
Nodes beitreten – der Key wird beim Join als `--certificate-key` angegeben und ist ~2h gültig):

```bash
sudo kubeadm init phase upload-certs --upload-certs
```

Join-Befehl mit frischem Token ausgeben:

```bash
sudo kubeadm token create --print-join-command
```

- Der ausgegebene Befehl (`kubeadm join 142.132.246.190:6443 --token ... --discovery-token-ca-cert-hash sha256:...`)
  wird auf dem beitretenden Node ausgeführt.
- Für einen **Worker-Join** reicht dieser Befehl.
- Für einen **Control-Plane-Join** (z. B. `gibbs-kube`) wird zusätzlich
  `--control-plane --certificate-key <KEY>` angehängt, wobei `<KEY>` aus dem
  `upload-certs`-Schritt stammt.

> Token und Certificate-Key sind Secrets und laufen ab (Token standardmäßig 24h,
> Certificate-Key ~2h) – bei Bedarf neu erzeugen. Nicht ins Repo committen.

## 13. Weiteren Control-Plane-Node beitreten (gibbs)

`gibbs-kube` tritt dem Cluster als **Control-Plane-Node** bei. Voraussetzung: Schritte 1–6
(Basis-Setup) sind auf gibbs durchgeführt; Schritt 5 (netplan/VLAN) entfällt, da gibbs ein
Cloud-Server ist.

Auf `barbossa` einen frischen Certificate-Key erzeugen (Ausgabe: `Using certificate key: <KEY>`,
~2h gültig):

```bash
sudo kubeadm init phase upload-certs --upload-certs
```

Auf `gibbs` den Join mit Control-Plane-Flags ausführen (`--certificate-key` aus dem
`upload-certs`-Schritt, Token/Hash aus `kubeadm token create --print-join-command`):

```bash
sudo kubeadm join 142.132.246.190:6443 \
  --token <TOKEN> \
  --discovery-token-ca-cert-hash sha256:<HASH> \
  --control-plane \
  --certificate-key <CERT_KEY>
```

> **Secrets:** `--token`, `--discovery-token-ca-cert-hash` und `--certificate-key` sind
> vertraulich und laufen ab (Token 24h, Cert-Key ~2h). Nicht ins Repo committen.

Nach dem Join auf `gibbs`:

- kubeconfig für den Admin-User einrichten (wie in Schritt 8):

  ```bash
  mkdir -p $HOME/.kube
  sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
  sudo chown $(id -u):$(id -g) $HOME/.kube/config
  ```

- Prüfen (auf einem beliebigen Control-Plane-Node):

  ```bash
  kubectl get nodes
  kubectl get pods -n kube-system -l component=etcd -o wide
  ```

> **Taints/Labels:** Die `--all`-Befehle aus Schritt 11 wirken nur auf zum Zeitpunkt der
> Ausführung vorhandene Nodes. Nach dem Join von `gibbs` ggf. erneut ausführen, damit auch
> gibbs für Workloads freigegeben und als Worker gelabelt ist.

> **etcd-Quorum-Hinweis:** Mit **zwei** Control-Plane-Nodes hat etcd Quorum 2 – fällt ein
> Node aus, verliert der Cluster das Quorum (keine Ausfalltoleranz). Für echtes HA werden
> **drei** Control-Plane-Nodes empfohlen (siehe geplanter 3. Node).

## 14. Flux-Operator installieren (Control-Plane / Root-Server)

Als Einstieg in den GitOps-Betrieb wird der **flux-operator** von controlplane.io per Helm
(OCI-Chart) installiert. Auf `barbossa-kube`:

```bash
helm install flux-operator oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator \
  --namespace flux-system \
  --create-namespace
```

- Der flux-operator verwaltet die Flux-Controller über eine `FluxInstance`-CR (statt des
  klassischen `flux bootstrap`).
- Namespace `flux-system` wird angelegt, falls noch nicht vorhanden.

> Nächster Schritt: eine `FluxInstance`-Ressource anlegen, die auf dieses Git-Repository
> zeigt (Cluster-Pfad `clusters/<cluster-name>`), damit Flux die Infrastruktur aus dem Repo
> synchronisiert.

## Nächste Schritte

_Wird ergänzt (FluxInstance / Repo-Anbindung, 3. Node …)._
