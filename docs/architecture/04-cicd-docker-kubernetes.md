# ICI Platform — CI/CD Pipeline, Docker & Kubernetes

## 8. CI/CD Pipeline

### 8.1 Pipeline Architecture

```
Developer Push
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│               GitHub Actions CI Pipeline                │
│                                                         │
│  On PR:                                                 │
│  1. affected-detection (Nx)                             │
│  2. lint + type-check (parallel per service)            │
│  3. unit tests + coverage gate (≥ 85%)                  │
│  4. integration tests (testcontainers)                  │
│  5. security scan (Trivy + Semgrep + OWASP dep-check)   │
│  6. contract tests (Pact)                               │
│  7. docker build (multi-arch: amd64 + arm64)            │
│  8. image scan (Trivy)                                  │
│                                                         │
│  On merge to main:                                      │
│  9.  push to ECR (SHA + latest tags)                    │
│  10. deploy to DEV (Argo CD sync)                       │
│  11. smoke tests (k6, 5 min)                            │
│  12. deploy to STAGING (Argo CD sync, manual gate)      │
│  13. full load test (k6, 30 min)                        │
│  14. MAPE accuracy gate (PFE)                           │
│  15. deploy to PROD (Argo CD sync, approval required)   │
│  16. canary rollout 5% → 25% → 100%                    │
│  17. post-deploy smoke + synthetic monitoring           │
└─────────────────────────────────────────────────────────┘
```

### 8.2 GitHub Actions — CI Workflow

```yaml
# .github/workflows/ci.yml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

env:
  PYTHON_VERSION: "3.12"
  REGISTRY: ${{ secrets.AWS_ACCOUNT_ID }}.dkr.ecr.eu-central-1.amazonaws.com
  NX_CLOUD_ACCESS_TOKEN: ${{ secrets.NX_CLOUD_ACCESS_TOKEN }}

jobs:
  affected:
    runs-on: ubuntu-latest
    outputs:
      services: ${{ steps.nx.outputs.affected }}
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: pnpm install --frozen-lockfile
      - id: nx
        run: |
          AFFECTED=$(npx nx show projects --affected --json)
          echo "affected=$AFFECTED" >> $GITHUB_OUTPUT

  lint-test:
    needs: affected
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service: ${{ fromJson(needs.affected.outputs.services) }}
      fail-fast: false
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}" }
      - uses: astral-sh/setup-uv@v3

      - name: Install dependencies
        run: uv sync --project services/${{ matrix.service }}

      - name: Lint
        run: |
          cd services/${{ matrix.service }}
          uv run ruff check src/ tests/
          uv run mypy src/ --strict

      - name: Unit tests
        run: |
          cd services/${{ matrix.service }}
          uv run pytest tests/unit/ -v \
            --cov=src --cov-report=xml \
            --cov-fail-under=85 \
            -p no:warnings

      - name: Integration tests
        run: |
          cd services/${{ matrix.service }}
          uv run pytest tests/integration/ -v \
            --timeout=120 \
            -m "not slow"
        env:
          TESTCONTAINERS_RYUK_DISABLED: "true"

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          files: services/${{ matrix.service }}/coverage.xml
          flags: ${{ matrix.service }}

  security-scan:
    needs: affected
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Semgrep SAST
        uses: semgrep/semgrep-action@v1
        with:
          config: >
            p/python
            p/owasp-top-ten
            p/sql-injection
            p/secrets

      - name: OWASP Dependency Check
        uses: dependency-check/Dependency-Check_Action@main
        with:
          project: ici-platform
          path: .
          format: HTML
          args: --failOnCVSS 7 --enableRetired

  docker-build:
    needs: [lint-test, security-scan]
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service: ${{ fromJson(needs.affected.outputs.services) }}
    steps:
      - uses: actions/checkout@v4

      - name: Set up QEMU (multi-arch)
        uses: docker/setup-qemu-action@v3

      - name: Set up Buildx
        uses: docker/setup-buildx-action@v3

      - name: Login to ECR
        uses: aws-actions/amazon-ecr-login@v2
        with:
          region: eu-central-1

      - name: Build & push
        uses: docker/build-push-action@v5
        with:
          context: services/${{ matrix.service }}
          platforms: linux/amd64,linux/arm64
          push: ${{ github.event_name == 'push' }}
          tags: |
            ${{ env.REGISTRY }}/ici/${{ matrix.service }}:${{ github.sha }}
            ${{ env.REGISTRY }}/ici/${{ matrix.service }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max
          labels: |
            org.opencontainers.image.revision=${{ github.sha }}
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}

      - name: Scan image with Trivy
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: ${{ env.REGISTRY }}/ici/${{ matrix.service }}:${{ github.sha }}
          format: sarif
          severity: CRITICAL,HIGH
          exit-code: 1

  deploy-dev:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    needs: docker-build
    runs-on: ubuntu-latest
    environment: dev
    steps:
      - uses: actions/checkout@v4
      - name: Update image tags in dev overlay
        run: |
          cd infra/k8s/overlays/dev
          for svc in ${{ join(fromJson(needs.affected.outputs.services), ' ') }}; do
            kustomize edit set image \
              ${{ env.REGISTRY }}/ici/$svc=${{ env.REGISTRY }}/ici/$svc:${{ github.sha }}
          done
      - name: Commit image tag update
        run: |
          git config user.email "ci@ici-platform.com"
          git config user.name "ICI CI Bot"
          git add infra/k8s/overlays/dev/
          git commit -m "chore(dev): update images to ${{ github.sha }}" || true
          git push
      # Argo CD watches git → auto-syncs
```

### 8.3 Argo CD — GitOps Deployment

```yaml
# infra/k8s/base/argocd/application-set.yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: ici-services
  namespace: argocd
spec:
  generators:
    - list:
        elements:
          - service: material-engine
            namespace: ici
            port: "8001"
          - service: cost-engine
            namespace: ici
            port: "8005"
          - service: forecast-engine
            namespace: ici
            port: "8008"
          - service: risk-engine
            namespace: ici
            port: "8009"
  template:
    metadata:
      name: "{{service}}"
    spec:
      project: ici-platform
      source:
        repoURL: https://github.com/marksio90/industrial-cost-intelligence
        targetRevision: main
        path: infra/k8s/overlays/prod/{{service}}
      destination:
        server: https://kubernetes.default.svc
        namespace: "{{namespace}}"
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=true
          - ServerSideApply=true
        retry:
          limit: 5
          backoff:
            duration: 5s
            maxDuration: 3m
            factor: 2
```

---

## 9. Docker & Kubernetes

### 9.1 Base Dockerfile (Python services)

```dockerfile
# infra/docker/base-python/Dockerfile
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# --- Builder stage ---
FROM base AS builder
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

# --- Final stage ---
FROM base AS final

RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --shell /bin/sh --create-home appuser

COPY --from=builder --chown=appuser:appgroup /app /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "src.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--loop", "uvloop", "--http", "httptools"]
```

### 9.2 Service-specific Dockerfile (Cost Engine)

```dockerfile
# services/cost-engine/Dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE:-ici-platform/base-python:latest} AS base

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ ./src/
COPY migrations/ ./migrations/

FROM base AS final
USER appuser
EXPOSE 8005
LABEL org.opencontainers.image.title="ICI Cost Breakdown Engine" \
      org.opencontainers.image.vendor="ICI Platform"

CMD ["python", "-m", "uvicorn", "src.cost_engine.main:app", \
     "--host", "0.0.0.0", "--port", "8005", \
     "--workers", "4", "--loop", "uvloop"]
```

### 9.3 Kubernetes Deployment (Cost Engine)

```yaml
# infra/k8s/base/cost-engine/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cost-engine
  namespace: ici
  labels:
    app: cost-engine
    version: stable
spec:
  replicas: 2
  selector:
    matchLabels:
      app: cost-engine
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: cost-engine
        version: stable
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port:   "8005"
        prometheus.io/path:   "/metrics"
    spec:
      serviceAccountName: cost-engine-sa
      securityContext:
        runAsNonRoot: true
        runAsUser: 1001
        runAsGroup: 1001
        fsGroup: 1001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: cost-engine
          image: ${REGISTRY}/ici/cost-engine:${IMAGE_TAG}
          ports:
            - containerPort: 8005
              protocol: TCP
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: cost-engine-secrets
                  key: database_url
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: ici-shared-secrets
                  key: redis_url
            - name: KAFKA_BROKERS
              valueFrom:
                configMapKeyRef:
                  name: ici-kafka-config
                  key: brokers
            - name: LOG_LEVEL
              value: "INFO"
          resources:
            requests:
              memory: "512Mi"
              cpu: "250m"
            limits:
              memory: "1Gi"
              cpu: "1000m"
          readinessProbe:
            httpGet:
              path: /health/ready
              port: 8005
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health/live
              port: 8005
            initialDelaySeconds: 30
            periodSeconds: 30
            failureThreshold: 3
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: cost-engine
      terminationGracePeriodSeconds: 60
```

### 9.4 HPA Configuration

```yaml
# infra/k8s/base/cost-engine/hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: cost-engine-hpa
  namespace: ici
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: cost-engine
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
    - type: Pods
      pods:
        metric:
          name: cbe_active_breakdowns_total
        target:
          type: AverageValue
          averageValue: "50"
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 2
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Pods
          value: 1
          periodSeconds: 120
```

### 9.5 Kubernetes Namespace & RBAC

```yaml
# infra/k8s/base/namespaces.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: ici
  labels:
    istio-injection: enabled
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/warn: restricted
---
apiVersion: v1
kind: Namespace
metadata:
  name: ici-ml
  labels:
    istio-injection: enabled
---
apiVersion: v1
kind: Namespace
metadata:
  name: ici-data
  labels:
    istio-injection: enabled
---
# Service Account per service
apiVersion: v1
kind: ServiceAccount
metadata:
  name: cost-engine-sa
  namespace: ici
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::${AWS_ACCOUNT_ID}:role/ici-cost-engine
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: cost-engine-role
  namespace: ici
rules:
  - apiGroups: [""]
    resources: [secrets, configmaps]
    resourceNames: [cost-engine-secrets, ici-kafka-config, ici-shared-secrets]
    verbs: [get]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: cost-engine-rb
  namespace: ici
subjects:
  - kind: ServiceAccount
    name: cost-engine-sa
roleRef:
  kind: Role
  name: cost-engine-role
  apiGroup: rbac.authorization.k8s.io
```

### 9.6 Service Mesh — All Services HPA Matrix

| Service | Min | Max | CPU trigger | Custom metric |
|---------|-----|-----|-------------|---------------|
| api-gateway | 3 | 10 | 70% | req/s > 500 |
| material-engine | 2 | 6 | 70% | — |
| process-engine | 2 | 4 | 70% | — |
| supplier-engine | 2 | 6 | 70% | — |
| supplier-offer-parser | 2 | 8 | 70% | sop_queue_depth |
| cost-engine | 2 | 10 | 70% | active_breakdowns |
| bom-engine | 2 | 4 | 70% | — |
| cad-engine | 1 | 4 | 60% | GPU util (GPU nodepool) |
| forecast-engine | 2 | 6 | 70% | pfe_queue_depth |
| risk-engine | 2 | 6 | 70% | crae_analysis_queue |
| rfq-agent | 2 | 6 | 70% | — |
| learning-engine | 1 | 4 | 60% | GPU util (GPU nodepool) |

### 9.7 PodDisruptionBudget

```yaml
# Applied to every service
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: cost-engine-pdb
  namespace: ici
spec:
  minAvailable: 1   # or 50% for services with minReplicas ≥ 4
  selector:
    matchLabels:
      app: cost-engine
```

### 9.8 Network Policies

```yaml
# infra/k8s/base/network-policies/cost-engine-netpol.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: cost-engine-netpol
  namespace: ici
spec:
  podSelector:
    matchLabels:
      app: cost-engine
  policyTypes: [Ingress, Egress]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: api-gateway
        - podSelector:
            matchLabels:
              app: rfq-agent
        - podSelector:
            matchLabels:
              app: risk-engine
      ports:
        - protocol: TCP
          port: 8005
    - from:  # Prometheus scraping
        - namespaceSelector:
            matchLabels:
              name: monitoring
      ports:
        - protocol: TCP
          port: 8005
  egress:
    - to:   # PostgreSQL RDS
        - ipBlock:
            cidr: 10.0.10.0/24  # RDS subnet
      ports:
        - protocol: TCP
          port: 5432
    - to:   # Redis
        - podSelector:
            matchLabels:
              app: redis
      ports:
        - protocol: TCP
          port: 6379
    - to:   # Kafka MSK
        - ipBlock:
            cidr: 10.0.20.0/24
      ports:
        - protocol: TCP
          port: 9092
        - protocol: TCP
          port: 9093
    - to:   # DNS
        - namespaceSelector: {}
      ports:
        - protocol: UDP
          port: 53
```

### 9.9 Kustomize Overlays

```yaml
# infra/k8s/overlays/prod/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: ici
namePrefix: ""

resources:
  - ../../base

patches:
  - target:
      kind: Deployment
      name: cost-engine
    patch: |-
      - op: replace
        path: /spec/replicas
        value: 4
      - op: replace
        path: /spec/template/spec/containers/0/resources/requests/memory
        value: "1Gi"
      - op: replace
        path: /spec/template/spec/containers/0/resources/limits/memory
        value: "2Gi"

images:
  - name: cost-engine
    newName: ${REGISTRY}/ici/cost-engine
    newTag: ${IMAGE_TAG}

configMapGenerator:
  - name: ici-env-config
    literals:
      - ENVIRONMENT=production
      - LOG_LEVEL=WARNING
      - ENABLE_CACHE=true

secretGenerator:
  - name: cost-engine-secrets
    envs:
      - secrets/cost-engine.env
    options:
      disableNameSuffixHash: true
```
