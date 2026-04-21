# LexiAgent — Kubernetes Proof of Concept

Horizontally scalable deployment of the LexiAgent stack. Demonstrates the
compute tier (FastAPI + extraction + RAG) scaling out while the LLM tier
(Ollama or Anthropic) stays pinned or is delegated to a managed API.

## What's here

| File | Purpose |
|---|---|
| `00-namespace.yaml` | `lexiagent` namespace |
| `01-configmap.yaml` | Non-secret env: provider, model names, Ollama host |
| `02-secret.yaml` | **Template** for `ANTHROPIC_API_KEY` / `LEXIAGENT_API_KEY` — do not commit real values |
| `03-lexiagent.yaml` | Deployment (2 replicas) + ClusterIP Service w/ ClientIP affinity |
| `04-open-webui.yaml` | Open WebUI deployment + NodePort Service (`:30000`) + PVC |
| `05-ollama.yaml` | **Optional** in-cluster Ollama + PVC. Skip if using a host-level or managed Ollama |

## Prerequisites

- A cluster (kind, minikube, Docker Desktop K8s, or managed)
- `kubectl` configured against that cluster
- The `lexiagent:latest` image reachable by the cluster's nodes

## 1. Build and load the image

The manifests reference `image: lexiagent:latest` with `imagePullPolicy: IfNotPresent`,
so the image needs to exist on the node — no registry required for a local POC.

```bash
# From repo root
docker build -t lexiagent:latest .

# kind
kind load docker-image lexiagent:latest

# minikube
minikube image load lexiagent:latest

# Docker Desktop K8s — already shared, no action needed
```

For a real cluster, push to a registry and update `image:` in `03-lexiagent.yaml`.

## 2. Create the namespace and secret

```bash
kubectl apply -f k8s/00-namespace.yaml

# Preferred: create the secret imperatively so real keys never land in git
kubectl -n lexiagent create secret generic lexiagent-secrets \
    --from-literal=ANTHROPIC_API_KEY="sk-ant-..." \
    --from-literal=LEXIAGENT_API_KEY=""
```

`LEXIAGENT_API_KEY` is an optional bearer token protecting `/v1/*`. Leave
empty for an internal-only POC.

## 3. Pick an Ollama source

**Option A — in-cluster Ollama (self-contained, CPU):**

```bash
kubectl apply -f k8s/05-ollama.yaml
# Wait for the pod, then pull models into its volume:
kubectl -n lexiagent rollout status deploy/ollama
kubectl -n lexiagent exec deploy/ollama -- ollama pull qwen3:4b
kubectl -n lexiagent exec deploy/ollama -- ollama pull nomic-embed-text
```

Leave `OLLAMA_HOST: "http://ollama:11434"` in `01-configmap.yaml` (the default).

**Option B — host-level Ollama on the node (leverages node GPU):**

Skip `05-ollama.yaml`. In `01-configmap.yaml`, set:

```yaml
OLLAMA_HOST: "http://host.docker.internal:11434"   # Docker Desktop
# or the node IP reachable from pods
```

**Option C — Anthropic Claude (no Ollama required):**

In `01-configmap.yaml`, set `LLM_PROVIDER: "anthropic"`. Extraction calls
go to Claude via `ANTHROPIC_API_KEY` from the secret. Embeddings for RAG
still need Ollama with `nomic-embed-text` — use Option A or B alongside.

## 4. Apply the rest

```bash
kubectl apply -f k8s/01-configmap.yaml
kubectl apply -f k8s/03-lexiagent.yaml
kubectl apply -f k8s/04-open-webui.yaml
```

## 5. Verify

```bash
kubectl -n lexiagent get pods
# NAME                          READY   STATUS    RESTARTS
# lexiagent-xxxxxxxxxx-aaaaa    1/1     Running   0
# lexiagent-xxxxxxxxxx-bbbbb    1/1     Running   0
# open-webui-xxxxxxxxxx-ccccc   1/1     Running   0
# ollama-xxxxxxxxxx-ddddd       1/1     Running   0   (if Option A)

kubectl -n lexiagent port-forward svc/lexiagent 8001:8001 &
curl localhost:8001/health
# {"status":"ok"}
```

Open WebUI: `http://<node-ip>:30000` (NodePort).

## Horizontal scaling demo

```bash
kubectl -n lexiagent scale deploy/lexiagent --replicas=4
kubectl -n lexiagent get pods -l app=lexiagent -w
```

Rolling updates keep availability (`maxUnavailable: 0`). Health probes
gate traffic on `/health`.

## Session-state limitation (the important caveat)

`app/api_server.py` keeps conversation state in an in-process
`_SESSION_STATES` dict. With multiple replicas, a follow-up message from
the same user must land on the same pod that served the first message,
or the session is lost.

The Service in `03-lexiagent.yaml` sets:

```yaml
sessionAffinity: ClientIP
sessionAffinityConfig:
  clientIP:
    timeoutSeconds: 3600
```

This pins a client IP to one replica for an hour — enough for a POC
demo. **Production path:** externalize `_SESSION_STATES` to Redis and
drop `sessionAffinity`. That turns the compute tier into a fully
stateless, freely scalable pool.

## Teardown

```bash
kubectl delete namespace lexiagent
```

Removes everything including the PVCs.
