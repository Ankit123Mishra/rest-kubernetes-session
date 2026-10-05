# Brew Bar

Two tiny FastAPI services for teaching REST and Kubernetes.

| Service | Owns | Talks to | Local port |
|---|---|---|---|
| `menu-service` | menu items (full CRUD, auth, probes) | nobody | 8001 |
| `order-service` | orders | `menu-service` over HTTP | 8002 |

```
brewbar/
├── menu-service/app/main.py    ← every REST lesson, one per route
├── order-service/app/main.py   ← service-to-service call + error mapping
├── */Dockerfile
├── k8s/
│   ├── 00-namespace.yaml
│   ├── 01-bare-pod.yaml        ← "deploy into a pod" in its simplest form
│   ├── 02-menu-service.yaml    ← Secret + Deployment (2 replicas) + ClusterIP Service
│   └── 03-order-service.yaml   ← ConfigMap + Deployment + NodePort Service
└── demo.sh                     ← guided curl walkthrough
```

## Part 1 · Run locally (no containers)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r order-service/requirements.txt   # superset of menu's deps

(cd menu-service  && uvicorn app.main:app --reload --port 8001) &
(cd order-service && uvicorn app.main:app --reload --port 8002) &

open http://localhost:8001/docs
./demo.sh
```

## Part 2 · Build images

```bash
docker build -t brewbar/menu-service:1.0  menu-service
docker build -t brewbar/order-service:1.0 order-service
docker run --rm -p 8001:8000 brewbar/menu-service:1.0      # sanity check
```

## Part 3 · Deploy to Kubernetes

Any local cluster works. Make the images visible to it first:

| Cluster | Load images |
|---|---|
| Docker Desktop (Kubernetes enabled) | nothing to do, it shares Docker's images |
| kind | `kind create cluster --name brewbar` then `kind load docker-image brewbar/menu-service:1.0 brewbar/order-service:1.0 --name brewbar` |
| minikube | `minikube image load brewbar/menu-service:1.0` and the same for order-service |

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl config set-context --current --namespace=brewbar

# A bare pod
kubectl apply -f k8s/01-bare-pod.yaml
kubectl get pods -o wide
kubectl port-forward pod/menu-solo 8001:8000      # then curl localhost:8001/menu
kubectl delete pod menu-solo                       # gone, nobody restarts it

# A real deployment
kubectl apply -f k8s/02-menu-service.yaml -f k8s/03-order-service.yaml
kubectl get deploy,pods,svc,endpoints
kubectl logs -l app=menu-service -f
```

### Demo moments

**1. Load balancing.** Run this a few times and watch the pod name change:

```bash
kubectl port-forward svc/order-service 8002:80 &
curl -s -X POST localhost:8002/orders -H 'Content-Type: application/json' \
  -d '{"customer":"Asha","item_id":2}' | grep priced_by_pod
```

`port-forward` to a Service pins to one pod, but calls *from* order-service *to* menu-service go through the Service, so `priced_by_pod` rotates.

**2. Service discovery is just DNS.**

```bash
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -- sh
# inside the debug pod:
nslookup menu-service
curl -s http://menu-service/whoami
curl -s http://menu-service.brewbar.svc.cluster.local/whoami
```

**3. Self-healing.** Kill a pod and watch the Deployment replace it:

```bash
kubectl get pods -w &
kubectl delete pod -l app=menu-service --wait=false
```

**4. Readiness vs liveness.** Take one menu pod out of rotation without killing it:

```bash
kubectl get endpoints menu-service -w &
POD=$(kubectl get pod -l app=menu-service -o name | head -1)
kubectl exec $POD -- python -c "import urllib.request as u; r=u.Request('http://localhost:8000/admin/toggle-ready', method='POST', headers={'X-API-Key':'barista-secret'}); print(u.urlopen(r).read())"
# one IP disappears from the endpoints list; run the same command again to bring it back
```

**5. Why services must be stateless (the planned "bug").** Each menu pod keeps its own in-memory dict. POST a new item through a pod, then order it a few times: some orders succeed, some get 422, depending on which pod answers. The fix is shared state: a database outside the pods.

**6. Scaling.**

```bash
kubectl scale deploy/menu-service --replicas=4
kubectl get pods -l app=menu-service
```

## Clean up

```bash
kubectl delete namespace brewbar
```
