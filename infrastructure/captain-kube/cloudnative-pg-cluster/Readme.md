ToDo:

Lege einen S3 Bucket an mit dem Namen cloudnative-pg (kann in der helm-release.yaml geändert werden)
Erzeuge einen Access Key ID Und Access Key Secret und hinterlege es in der cluster-secret-encrypted.env mit folgenden keys:
- minio-key-id
- minio-access-key

Danach kannst du in der cluster.yaml neue Rollen anlegen, die wie folgt aussehen können:
```YAML
spec:
  managed:
    # Anlegen von Rollen
    roles:
      - name: keycloak
        login: true
        comment: keycloak user
        disablePassword: false
        passwordSecret:
          name: keycloak-secret
```

Mit dem Database CRD kann man dann auch Datenbanken anlegen lassen:

```YAML
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: keycloak
spec:
  # Name der Datenbank
  name: keycloak
  # Name des Owners der Datenbank
  owner: keycloak
  cluster:
    # Name des cloudnative-pg clusters
    name: postgres-cluster
  # Default Encoding der Datenbank
  encoding: UTF8
```