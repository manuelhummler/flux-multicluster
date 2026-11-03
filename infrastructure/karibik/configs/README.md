# karibik – cluster-secrets & SOPS (Phase 0, Schritt 1)

Dieser Ordner erzeugt das Secret `cluster-secrets` im `flux-system`-Namespace.
Fast alle Apps beziehen daraus per `postBuild.substituteFrom` ihre Werte
(`${DOMAIN}`, `${EMAIL}`, `${API_SERVER_IP}`, …). **Ohne diesen Schritt
schlagen alle Kustomizations mit `substituteFrom` fehl.**

## Einmalig: age-Key im Cluster hinterlegen

Flux entschlüsselt SOPS-Dateien mit einem Secret namens `sops-age` im
`flux-system`-Namespace. Das muss out-of-band (nicht via Git) in den
**karibik-Cluster** eingespielt werden:

```bash
# Variante A: denselben Key wie captain-kube wiederverwenden
#   (passend zum Recipient in ../.sops.yaml)
kubectl --context karibik -n flux-system create secret generic sops-age \
  --from-file=age.agekey=/pfad/zu/deinem.agekey

# Variante B: eigener karibik-Key
#   age-keygen -o karibik.agekey   -> Public-Key in ../.sops.yaml eintragen
#   dann wie oben als sops-age einspielen
```

> `*.agekey` ist in `.gitignore` – niemals committen.

## Secret befüllen und verschlüsseln

```bash
cd infrastructure/karibik/configs
cp secret.env.example secret-encrypted.env
# Werte eintragen (DOMAIN, EMAIL, ... – siehe Kommentare in der Datei)
sops --encrypt --in-place secret-encrypted.env   # nutzt ../.sops.yaml
git add secret-encrypted.env                       # nur verschlüsselt committen!
```

Danach reconciled Flux den `configs`-Block aus
`clusters/karibik/infrastructure.yaml` und legt `cluster-secrets` an.
