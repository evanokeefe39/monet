# Split Fleet on Railway

Deploy the split-fleet example as **three Railway services** plus
managed Postgres and Redis plugins. All three services share the same
repo root (`examples/split-fleet/`) and differ only in which
`railway.toml` they use as their Config File.

## Services

| Service | Config File | Start command |
|---|---|---|
| `monet-server` | `railway/server.toml` | `aegra serve --host 0.0.0.0 --port ${PORT:-2026}` |
| `worker-fast` | `railway/worker-fast.toml` | `monet worker --server-url $MONET_SERVER_URL --pool fast --path agents` |
| `worker-heavy` | `railway/worker-heavy.toml` | `monet worker --server-url $MONET_SERVER_URL --pool heavy --path agents` |

All three: **Root Directory = `examples/split-fleet/`**.

## Walkthrough

1. Fork the repo (or push to your own).
2. Create a new project on [Railway](https://railway.com).
3. Add the **Postgres** plugin. Railway exposes `DATABASE_URL` to all
   services automatically.
4. Add the **Redis** plugin. Railway exposes `REDIS_URL` to all
   services automatically.
5. Create the **monet-server** service:
   - Connect the repo.
   - Settings → Source → Root Directory = `examples/split-fleet/`.
   - Settings → Config-as-Code → Config File = `railway/server.toml`.
   - Variables: `MONET_API_KEY`, `MONET_QUEUE_BACKEND=redis`. Postgres
     and Redis URLs are auto-injected from the plugins.
6. Create the **worker-fast** service:
   - Same repo, same Root Directory.
   - Config File = `railway/worker-fast.toml`.
   - Variables: `MONET_API_KEY` (same as server),
     `MONET_SERVER_URL=https://<monet-server-url>.up.railway.app`,
     `MONET_QUEUE_BACKEND=redis`.
7. Create the **worker-heavy** service: same as worker-fast but with
   Config File = `railway/worker-heavy.toml`.
8. Deploy all three.

## Pre-baked Aegra config

The server uses `examples/split-fleet/railway/aegra.json`, which points
at the demo fan-out graph plus monet's four default graphs. The
server's working directory is `examples/split-fleet/`, so the relative
paths resolve as `railway/server_graphs.py:...`.

If you add more graphs, edit `railway/aegra.json` (and re-deploy the
server service).

## Verify

Once deployed, from your machine:

```bash
export MONET_API_KEY="<same secret>"
monet run --url https://<monet-server-url>.up.railway.app --graph demo "railway split-fleet test"
```

You should see the fast result return immediately; the heavy result
returns after ~5 seconds. Check each worker service's logs in the
Railway dashboard to see which claimed which agent.
