# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a FluxCD GitOps repository for managing Kubernetes infrastructure across multiple clusters. It follows the multi-cluster pattern with a base/overlay structure using Kustomize.

## Repository Structure

```
clusters/
  captain-kube/           # Cluster-specific Flux configuration
    flux-system/          # Flux bootstrap components (DO NOT EDIT gotk-*.yaml manually)
    infrastructure.yaml   # Main orchestration file - defines deployment order
    hopps-*.yaml          # External app deployments from separate git repos

infrastructure/
  base/
    apps/                 # Reusable application definitions (HelmRelease, namespace, etc.)
    helm-repository/      # HelmRepository sources (flux-system namespace)
  captain-kube/           # Cluster-specific overlays and secrets
    .sops.yaml            # SOPS encryption rules for this cluster
```

## Key Patterns

### Adding a New Application

1. Create base definition in `infrastructure/base/apps/<app-name>/`:
   - `namespace.yaml` - Namespace resource
   - `helm-release.yaml` - HelmRelease with chart reference
   - `kustomization.yaml` - References helm-repository and local files

2. Add HelmRepository if needed in `infrastructure/base/helm-repository/`

3. Create cluster overlay in `infrastructure/captain-kube/<app-name>/`:
   - `kustomization.yaml` - References `../../base/apps/<app-name>`
   - Any cluster-specific patches or encrypted secrets

4. Add Kustomization entry in `clusters/captain-kube/infrastructure.yaml`:
   - Define `dependsOn` for deployment ordering
   - Add `healthChecks` for helm releases
   - Configure `decryption` if SOPS-encrypted secrets are used
   - Use `postBuild.substituteFrom` for variable substitution from cluster-secrets

### Secret Management

- Secrets are encrypted using SOPS with age encryption
- Encrypted files pattern: `*-encrypted.yaml` or `*-encrypted.env`
- Decrypted files are gitignored: `*decrypted.yaml`, `*decrypted.env`
- Age keys (`*.agekey`) are never committed
- Encryption rules defined in `infrastructure/captain-kube/.sops.yaml`

### Variable Substitution

Many Kustomizations use `postBuild.substituteFrom` referencing a `cluster-secrets` Secret for environment-specific values.

## Linting

MegaLinter runs daily via GitHub Actions (`.github/workflows/mega-linter.yml`):
- Disabled linters: JSON_JSONLINT, JSON_V8R, YAML_V8R, REPOSITORY_CHECKOV, REPOSITORY_DEVSKIM
- COPYPASTE and SPELL checks disabled
- KICS and Trivy security scanners run in warning mode

## Dependencies

Automated dependency updates via Renovate (`renovate.json`) for Flux infrastructure files.

## Deployed Applications

Key infrastructure components managed in this repo:
- **Networking**: Cilium (CNI), ingress-nginx
- **Storage**: Longhorn, MinIO
- **Databases**: PostgreSQL (postgres-operator + CloudNative-PG), MariaDB (mariadb-operator)
- **Security**: cert-manager, Keycloak, Vaultwarden, oauth2-proxy
- **Monitoring**: kube-prometheus-stack, loki-stack, metrics-server
- **Other**: Kubernetes Dashboard, Dependency-Track, Kimai, Apicurio Studio/Registry
