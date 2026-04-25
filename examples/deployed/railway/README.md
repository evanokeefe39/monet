# Deploy to Railway

Two-service S2 deployment: Aegra server + monet worker, backed by Railway-managed Postgres and Redis.

## Prerequisites

- Railway account and the [Railway CLI](https://docs.railway.app/develop/cli) (`npm i -g @railway/cli`)
- Repo pushed to GitHub (Railway deploys from git)
- `MONET_API_KEY`: generate one (`openssl rand -hex 32`)

## Steps

### 1. Create the project

```bash
railway login
railway init          # creates a new project
```

Or create via the Railway dashboard.

### 2. Add managed services

In the Railway dashboard for your project:

1. **Add Postgres** — click "+ New" → "Database" → "PostgreSQL". Railway injects `DATABASE_URL` automatically.
2. **Add Redis** — click "+ New" → "Database" → "Redis". Railway injects `REDIS_URL`. You must map it:
   - In the server service variables, add: `REDIS_URI=${{Redis.REDIS_URL}}`
   - In the worker service variables, add: `REDIS_URI=${{Redis.REDIS_URL}}`

### 3. Create the server service

1. Click "+ New" → "GitHub Repo" → select your fork.
2. Set **Root Directory** to `examples/deployed/server`. Railway reads `railway.toml` from there.
3. Set environment variables:

   | Variable | Value |
   |---|---|
   | `MONET_API_KEY` | your shared secret |
   | `MONET_QUEUE_BACKEND` | `redis` |
   | `MONET_DISTRIBUTED` | `1` |
   | `REDIS_URI` | `${{Redis.REDIS_URL}}` |
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
   | `GEMINI_API_KEY` | your key |
   | `GROQ_API_KEY` | your key |

4. Deploy.

### 4. Create the worker service

1. Click "+ New" → "GitHub Repo" → same repo.
2. Set **Root Directory** to `examples/deployed/worker`. Railway reads `railway.toml` from there.
3. Set environment variables:

   | Variable | Value |
   |---|---|
   | `MONET_SERVER_URL` | public URL of the server service (e.g. `https://server-production-xxxx.up.railway.app`) |
   | `MONET_API_KEY` | same shared secret |
   | `MONET_QUEUE_BACKEND` | `redis` |
   | `MONET_DISTRIBUTED` | `1` |
   | `MONET_WORKER_POOL` | `default` |
   | `MONET_WORKER_CONCURRENCY` | `10` |
   | `REDIS_URI` | `${{Redis.REDIS_URL}}` |
   | `GEMINI_API_KEY` | your key |
   | `GROQ_API_KEY` | your key |

4. Deploy.

### 5. Verify

```bash
# Health check
curl https://<server-url>.up.railway.app/health

# Confirm worker registered
export MONET_API_KEY="<your-key>"
monet status --url https://<server-url>.up.railway.app
```

## Connect from your laptop

```bash
export MONET_API_KEY="your-secret"
export MONET_SERVER_URL="https://<server-url>.up.railway.app"

# Run a job on the Railway server
monet run "AI trends in healthcare" --auto-approve

# Or run a local worker alongside the Railway worker
monet worker --pool default --path ./my-agents
```

## Redis variable mapping

Railway's Redis plugin exposes `REDIS_URL` (the `redis://` URL). Monet reads `REDIS_URI`. Use Railway's variable reference syntax to map it:

```
REDIS_URI=${{Redis.REDIS_URL}}
```

If using Upstash, paste the TCP connection string directly into `REDIS_URI` (not the REST URL — the queue needs `XREADGROUP` and Pub/Sub).
