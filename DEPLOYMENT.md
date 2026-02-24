# Dr. Vroom Brain Server — Deployment Guide
# 닥터브릉이 서버 배포 가이드

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Dr. Vroom System                          │
│                                                              │
│  📱 Client App          📚 Trainer App                       │
│  (Flutter Android/iOS)  (Flutter Android/iOS)                │
│       │                        │                             │
│       └────────────┬───────────┘                             │
│                    │ REST API + WebSocket                     │
│                    ▼                                         │
│  🧠 Dr. Vroom Brain Server (FastAPI)                         │
│     ├── /api/v1/auth        ← JWT authentication             │
│     ├── /api/v1/diagnosis   ← Sound analysis                 │
│     ├── /api/v1/knowledge   ← Knowledge management          │
│     ├── /ws/{role}/{id}     ← WebSocket (1023 max)          │
│     └── SQLite DB           ← Knowledge + Sessions           │
└─────────────────────────────────────────────────────────────┘
```

## 🚀 Deployment Options (Minimal Cost)

### Option 1: Railway.app (Recommended — Free Tier)
- **Free**: $5/month credit (enough for 1023 users)
- **WebSocket**: Supported
- **Auto-deploy**: GitHub integration

```bash
# Install Railway CLI
npm install -g @railway/cli

# Deploy
railway login
railway init
railway up
```

### Option 2: Render.com (Free Tier)
- **Free**: 750 hours/month
- **Note**: Sleeps after 15 min inactivity (use paid $7/mo for always-on)

```bash
# render.yaml is included in repo
render deploy
```

### Option 3: Fly.io (Generous Free Tier)
- **Free**: 3 shared VMs, 256MB RAM each
- **WebSocket**: Supported

```bash
fly launch
fly deploy
```

### Option 4: Self-hosted VPS ($3-5/month)
- **Hetzner CX11**: €3.79/month, 2GB RAM — handles 1023 users easily
- **DigitalOcean Droplet**: $4/month

```bash
# On VPS
sudo apt update && sudo apt install -y python3 python3-pip nginx
pip3 install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## 📊 Capacity Analysis

| Users | RAM Usage | CPU | SQLite OK? |
|-------|-----------|-----|------------|
| 100   | ~50MB     | <5% | ✅         |
| 500   | ~150MB    | 10% | ✅         |
| 1023  | ~300MB    | 20% | ✅         |

**Conclusion**: A $4-5/month VPS handles all 1023 users comfortably.

## 🔧 Local Development

```bash
cd dr_vroom_server
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API Docs: http://localhost:8000/docs

## 🔒 Environment Variables

```env
SECRET_KEY=your-super-secret-key-here
DATABASE_URL=sqlite+aiosqlite:///./dr_vroom_brain.db
MAX_CONNECTIONS=1023
```

For PostgreSQL (production scale):
```env
DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname
```

## 📡 WebSocket Roles

| Role    | Description           | Max Connections |
|---------|-----------------------|-----------------|
| client  | Vehicle diagnosis     | 1000            |
| trainer | Knowledge teaching    | 20              |
| expert  | Expert verification   | 3               |

Total: 1023 max concurrent connections
