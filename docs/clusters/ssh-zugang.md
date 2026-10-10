# SSH-Zugang zu den Cluster-Nodes

Lokale SSH-Client-Konfiguration, damit auf jedem Admin-Rechner ein `ssh barbossa` bzw.
`ssh gibbs` reicht. Die Config gilt für Windows (OpenSSH / Git Bash), macOS und Linux
gleichermaßen.

> Admin-User auf den Nodes ist `hummli` (siehe [node-setup-hetzner.md](node-setup-hetzner.md),
> Schritt 6b). Login ausschließlich per SSH-Key – Passwort-Auth ist nach der
> [SSH-Härtung](firewall-hetzner.md#ssh-härtung-pflicht-da-port-22-weltweit-offen-ist) abgeschaltet.

## 1. SSH-Key (einmalig pro Rechner)

Falls noch kein Key vorhanden ist (`ls ~/.ssh/`), einen `ed25519`-Key erzeugen:

```bash
ssh-keygen -t ed25519 -C "hummli@$(hostname)"
```

Den **Public Key** (`~/.ssh/id_ed25519.pub`) auf jedem Node in
`/home/hummli/.ssh/authorized_keys` eintragen. Solange Passwort-Login noch erlaubt ist, geht das
per `ssh-copy-id`; danach den Key über eine bestehende Key-Session nachtragen:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub hummli@116.202.230.235   # barbossa
ssh-copy-id -i ~/.ssh/id_ed25519.pub hummli@88.99.225.74      # gibbs
```

Ohne `ssh-copy-id` (z. B. Git Bash):

```bash
cat ~/.ssh/id_ed25519.pub | ssh hummli@116.202.230.235 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

## 2. Client-Config

Datei `~/.ssh/config` anlegen bzw. ergänzen (Windows: `C:\Users\<user>\.ssh\config`, ohne
Dateiendung):

```sshconfig
# Karibik-Cluster (Hetzner) – siehe docs/clusters/README.md im flux-multicluster-Repo
Host barbossa barbossa-kube
    HostName 116.202.230.235
    User hummli

Host gibbs gibbs-kube
    HostName 88.99.225.74
    User hummli

# Gemeinsame Defaults für alle Karibik-Nodes
Host barbossa barbossa-kube gibbs gibbs-kube
    IdentityFile ~/.ssh/id_ed25519      # ⚠️ ggf. ~/.ssh/id_rsa, je nach vorhandenem Key
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

| Option                   | Zweck                                                                                     |
| ------------------------ | ----------------------------------------------------------------------------------------- |
| `User hummli`            | User muss nicht mehr angegeben werden                                                     |
| `IdentityFile` + `IdentitiesOnly yes` | Es wird genau dieser Key angeboten, nicht alle Keys aus dem Agent – wichtig, da der Server nach zu vielen falschen Keys abbricht und das nftables-Rate-Limit jeden Versuch zählt |
| `ServerAliveInterval 30` | Hält inaktive Sessions über Hetzner-NAT/Firewall offen                                    |

Unter Linux/macOS müssen die Rechte stimmen, sonst ignoriert `ssh` die Datei:

```bash
chmod 700 ~/.ssh && chmod 600 ~/.ssh/config
```

## 3. Benutzung

```bash
ssh barbossa
ssh gibbs
```

Die Aliase funktionieren überall, wo SSH im Spiel ist – z. B. beim Firewall-Rollout:

```bash
scp docs/clusters/nftables/karibik-node.nft barbossa:
```

## Neuen Node ergänzen

Beim 3. Node einen weiteren `Host`-Block anlegen **und** den Namen in die Sammelzeile
(`Host barbossa barbossa-kube gibbs gibbs-kube …`) aufnehmen, damit die Defaults greifen.
