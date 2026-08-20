# Deploying Plimsoll on Kubernetes

ADR-0003 chose Kubernetes-native generators. Docker is the development
runtime; this is the production one. The generator image is identical in both.

## What this chart does not do

It does not run Postgres, Redis, or the object store. A database as a chart
dependency is as disposable as the application in front of it, and the one
thing here that must not be disposable is the database. Point the chart at
what you already operate.

It creates no secrets. A password in `values.yaml` ends up in Git, in
`helm get values`, and in every CI log that renders the chart.

## Installing

```bash
kubectl create namespace plimsoll

kubectl -n plimsoll create secret generic plimsoll \
  --from-literal=databaseUrl='postgresql+asyncpg://plimsoll_app:...@host/plimsoll' \
  --from-literal=migrationDatabaseUrl='postgresql+asyncpg://plimsoll_owner:...@host/plimsoll' \
  --from-literal=redisUrl='redis://host:6379/0' \
  --from-literal=jwtSecret="$(openssl rand -base64 48)" \
  --from-literal=credentialKey="$(openssl rand -base64 32)" \
  --from-literal=s3Endpoint='https://s3.example.com' \
  --from-literal=s3AccessKey='...' --from-literal=s3SecretKey='...'

helm install plim infrastructure/kubernetes/plimsoll \
  --namespace plimsoll \
  --set ingress.enabled=true --set ingress.host=plimsoll.example.com
```

Migrations run as a `pre-install,pre-upgrade` hook, so they complete before the
new pods start and a failed migration fails the release. Running them from an
init container would instead run them once per replica, concurrently, against
one database.

## Two namespaces

Generators are created in `plimsoll-generators`, not alongside the control
plane. That separation does three things a single namespace cannot:

- A `ResourceQuota` caps load generation without capping the API. Without it, a
  test asking for more generators than the cluster has takes the cluster with
  it -- the control plane killed by the thing it controls.
- A generator cannot reach a control-plane service account.
- A `NetworkPolicy` denies generator egress by default. `generators.egress` is
  where the systems under test are named. The target policy check (ADR-0007)
  is the first gate and rejects at the API; this is the second, and it is the
  one an application bug cannot talk past.

The worker's own permissions are a `Role`, not a `ClusterRole`: create, delete
and list pods in the generator namespace, and nothing else. It cannot read
secrets in any namespace, cannot create pods beside the control plane, and
cannot touch nodes. `apps/worker/tests/integration/test_kubernetes_cluster.py`
asserts each of those against a live cluster.

## Why generators are bare pods

Not a Job, not a Deployment. Both exist to make work survive the loss of a
node, and invariant 6 says a generator must not: a Job retries under
`backoffLimit`, a Deployment reschedules, and either would restart a generator
mid-run. A restarted generator rejoins with an empty histogram and a fresh
ramp-up, so the run it rejoins reports numbers describing neither the old load
nor the new. Losing capacity is a result the system can report. Quietly
changing the experiment is not.

The pod name is derived from the run and the ordinal rather than generated, so
a worker that died between creating a pod and recording it collides on the
retry instead of silently doubling the load.

## One worker

`worker.replicas` is 1, and the deployment strategy is `Recreate` rather than
`RollingUpdate`. The reconciler is not partitioned: two workers would both act
on the same run, provisioning two sets of generators and each tearing down what
the other created. Scaling this needs leader election or sharding by run, and
neither exists yet.

## Verifying a cluster before trusting it

```bash
helm template plim infrastructure/kubernetes/plimsoll --namespace plimsoll \
  | kubectl apply --dry-run=server -f -
```

The runtime's own tests run against whatever cluster `kubectl` is pointed at,
and skip when there is none:

```bash
uv run pytest apps/worker/tests/integration/test_kubernetes_cluster.py
```

## What has not been proven here

The chart applies to a real API server and the runtime creates, finds, sizes
and removes pods on one. What has not been run on a cluster is a full load test
end to end -- that needs the generator image on the nodes, and it is what the
Docker integration suite exercises. The launcher is verified; the journey
through it on Kubernetes is not yet.
