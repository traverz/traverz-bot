# Traverz Bot — Onboarding Guide

High-level setup for running and developing the Traverz AI travel companion bot.

---

## 1. What You're Working With

Traverz Bot is an AI travel assistant that lives inside the Traverz mobile app. It helps users plan trips, manage itineraries, track budgets, search flights & hotels, and get packing recommendations. It's built on the [nanobot](https://github.com/HKUDS/nanobot) open-source agent framework.

Two artifacts are relevant:
- **The bot (`nanobot/`)**: Python agent that processes user messages, calls the Traverz backend API, and responds via WebSocket or other channels.
- **The WebUI (`webui/`)**: React/TypeScript chat client that connects to the bot over WebSocket (used for internal testing and demos).

---

## 2. Prerequisites

| Dependency | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | Managed via `uv` (pip-compatible) |
| Node.js | ≥ 20 | Only needed for the WebUI (`webui/`) and the WhatsApp bridge (`bridge/`) |
| Docker | latest | For containerised deployment |
| Gemini API key | — | Or any OpenAI-compatible provider key |

---

## 3. Environment Variables

The bot reads these at runtime. Set them in your shell or a `.env` file:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | — | Your Google Gemini API key (or use another provider) |
| `TRAVERZ_BACKEND_URL` | No | `https://api.traverz.ai` | Traverz backend API base URL |
| `TRAVERZ_SKILLS_REFRESH_S` | No | `300` | Seconds between skills manifest fetches |
| `WEBSOCKET_TOKEN_SECRET` | Yes* | — | Shared secret for issuing and validating WebSocket tokens (needed if `websocketRequiresToken: true`) |
| `TRIP_COM_ALLIANCE_ID` | No | — | Trip.com affiliate alliance ID |
| `TRIP_COM_SID` | No | — | Trip.com affiliate SID |
| `GETYOURGUIDE_PARTNER_ID` | No | — | GetYourGuide affiliate partner ID |
| `TRAVERZ_GCS_BUCKET` | No | — | GCS bucket name for persistent memory (Docker only) |

> \*`WEBSOCKET_TOKEN_SECRET` is required if `websocketRequiresToken` is `true` in config (default).

---

## 4. Configuration

The bot reads `~/.traverz/config.json` (or `~/.nanobot/config.json`). A default config is shipped at `traverz_config.json` and installed automatically by the Docker entrypoint.

**Key settings** in `traverz_config.json`:

```jsonc
{
  "providers": {
    "gemini": {
      "apiKey": "${GEMINI_API_KEY}",
      "apiBase": "https://generativelanguage.googleapis.com/v1beta/openai/"
    }
  },
  "agents": {
    "defaults": {
      "model": "gemini-3.1-flash-lite-preview",
      "maxTokens": 8192,
      "contextWindowTokens": 131072,
      "temperature": 0.3,
      "maxToolIterations": 20,
      "timezone": "Asia/Singapore",
      "disabledSkills": ["github", "tmux", "clawhub", "skill-creator"],
      "workspace": "/tmp/workspace"
    }
  },
  "tools": {
    "exec": { "enable": false },        // shell exec disabled for safety
    "web": { "enable": true },
    "restrictToWorkspace": true
  },
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8080,
      "path": "/ws"
    }
  }
}
```

To change the model, edit the `providers` block — any OpenAI-compatible endpoint works. The Gemini entry is just one example; you can use Anthropic, Azure OpenAI, Ollama, OpenRouter, etc.

---

## 5. Local Development Setup

### 5.1 Install the bot

```bash
# Clone and enter the repo
cd traverz-bot

# Create a virtual environment (or let uv handle it)
python3 -m venv .venv && source .venv/bin/activate

# Install in editable mode with dev deps
pip install -e ".[dev]"
```

### 5.2 Create your config

Copy the default config to your home directory and fill in your API key:

```bash
mkdir -p ~/.traverz
cp traverz_config.json ~/.traverz/config.json
# Edit ~/.traverz/config.json — replace placeholder values and add your API key
```

Alternatively, set `GEMINI_API_KEY` in your environment and the `${GEMINI_API_KEY}` placeholder in config will be resolved at startup.

### 5.3 Run the bot (CLI mode)

```bash
# Interactive CLI (chat in your terminal)
traverz

# Or use the nanobot alias (same binary)
nanobot
```

### 5.4 Run the bot (gateway mode — serves channels)

```bash
# Starts WebSocket server + any enabled channels
traverz gateway
```

This starts the WebSocket server on `ws://localhost:8080/ws` (as configured).

---

## 6. Running the WebUI

The WebUI is a React app that connects to the bot's WebSocket endpoint. Useful for testing the bot in a browser during development.

```bash
cd webui

# Install dependencies
npm install   # or: bun install

# Start dev server
npm run dev   # default: http://localhost:5173
```

The WebUI fetches a bootstrap endpoint from the bot (`/ws-token`) to get a WebSocket token, then connects. Make sure the bot is running in gateway mode first.

---

## 7. Docker Deployment

The production image is defined in `Dockerfile.traverz`. It includes:
- Python 3.12 + uv (package manager)
- Node.js 20 (for the WhatsApp bridge)
- gcsfuse (optional GCS bucket mount for persistent memory)
- Non-root `traverz` user

### 7.1 Build

```bash
docker build -f Dockerfile.traverz -t traverz-bot .
```

### 7.2 Run

```bash
# Without GCS (memory lives in the container)
docker run --rm -it \
  -p 8080:8080 \
  -e GEMINI_API_KEY="your-key" \
  -e WEBSOCKET_TOKEN_SECRET="your-secret" \
  traverz-bot gateway

# With GCS persistent memory
docker run --rm -it \
  -p 8080:8080 \
  -e GEMINI_API_KEY="your-key" \
  -e WEBSOCKET_TOKEN_SECRET="your-secret" \
  -e TRAVERZ_GCS_BUCKET="your-bucket" \
  --privileged \
  traverz-bot gateway
```

> `--privileged` is needed for gcsfuse; in production on Cloud Run or GKE this is handled by the platform.

### 7.3 Docker Compose (alt)

```bash
docker compose up
```

See `docker-compose.yml` for the standard nanobot setup; adapt for traverz by switching to `Dockerfile.traverz` and setting the right env vars.

---

## 8. How the Agent Gets Its Instructions

The agent's behavior is shaped by two files:

| File | Role |
|---|---|
| `traverz_agents.md` | High-level travel assistant instructions: response style, document-reading workflow, URL handling, confirmation-tag protocol (`[CONFIRM: ...]`) |
| `nanobot/skills/traverz/SKILL.md` | Comprehensive agent manual: identity, tone, operating modes, complete tool catalogue, step-by-step workflows for every operation (adding events, planning, budgets, packing, photo enrichment, flight/hotel search, proactive reminders) |

At startup, the Docker entrypoint copies `traverz_agents.md` → `/tmp/workspace/AGENTS.md` so the agent finds it. The SKILL.md is baked into the Python package and loaded because it has `always: true` in its frontmatter.

When updating agent instructions, edit these files — no code changes needed.

---

## 9. Testing

```bash
# Run the full test suite
pytest

# Run a specific area
pytest tests/agent/
pytest tests/channels/
pytest tests/tools/

# With coverage
pytest --cov=nanobot --cov-report=term-missing
```

Tests are structured under `tests/` mirroring the source layout: `tests/agent/`, `tests/channels/`, `tests/tools/`, `tests/providers/`, `tests/cli/`, `tests/utils/`, `tests/session/`, etc.

---

## 10. Project Layout Reference

```
traverz-bot/
├── nanobot/                    # Python agent (main codebase)
│   ├── agent/                  # Core loop, context, memory, hooks, tools, subagents
│   │   └── tools/traverz.py    # ★ 28+ Traverz backend tools
│   ├── channels/               # WebSocket, Telegram, Discord, Slack, WhatsApp, etc.
│   │   └── websocket.py        # ★ Primary channel for traverz
│   ├── cli/                    # CLI entry point (traverz / nanobot commands)
│   ├── config/                 # Config loader, schema, paths
│   ├── providers/              # LLM provider backends
│   ├── session/                # Session management
│   ├── skills/traverz/SKILL.md # ★ Agent instruction manual
│   ├── traverz/                # ★ Traverz-specific context vars + skills manifest
│   ├── utils/                  # Helpers, document parsing, exceptions
│   └── nanobot.py              # Python SDK facade
├── webui/                      # React/TypeScript chat UI
│   └── src/
│       ├── App.tsx             # Main app with WebSocket bootstrap
│       ├── components/         # UI components (Sidebar, ThreadShell, MarkdownText, sheets)
│       ├── hooks/              # useSessions, useTheme
│       ├── lib/                # NanobotClient, bootstrap, types, i18n
│       └── providers/          # ClientProvider context
├── bridge/                     # WhatsApp bridge (Node.js)
├── tests/                      # pytest suite
├── docs/                       # User/dev docs
├── traverz_config.json         # Default bot config
├── traverz_agents.md           # High-level agent instructions
├── traverz_entrypoint.sh       # Docker entrypoint script
├── Dockerfile.traverz          # Production Docker image
├── Dockerfile                  # Standard nanobot Docker image
├── docker-compose.yml          # Compose file
├── cloudbuild.yaml             # Cloud Build pipeline
├── deploy.sh                   # Deployment script
├── pyproject.toml              # Python project metadata + dependencies
└── README.md                   # Project README
```

---

## 11. Common Tasks

### Switch the LLM provider

Edit `~/.traverz/config.json` — add a new provider entry and change `agents.defaults.model` and `agents.defaults.provider`. Examples:

**Anthropic:**
```jsonc
"providers": {
  "anthropic": {
    "apiKey": "${ANTHROPIC_API_KEY}"
  }
},
"agents": { "defaults": { "model": "claude-sonnet-4-20250514", "provider": "anthropic" } }
```

**OpenAI:**
```jsonc
"providers": {
  "openai": {
    "apiKey": "${OPENAI_API_KEY}"
  }
},
"agents": { "defaults": { "model": "gpt-4.1", "provider": "openai" } }
```

**Local (Ollama / LM Studio):**
```jsonc
"providers": {
  "local": {
    "apiBase": "http://localhost:11434/v1"
  }
},
"agents": { "defaults": { "model": "llama3", "provider": "local" } }
```

### Add a new Traverz backend tool

1. Add a class in `nanobot/agent/tools/traverz.py` following the existing pattern (subclass `Tool`, define `name`, `description`, `parameters`, `execute`)
2. Add it to `ALL_TRAVERZ_TOOLS` at the bottom of the file
3. If it needs new context vars, add them to `nanobot/traverz/context.py`
4. Document it in `nanobot/skills/traverz/SKILL.md`

### Update the agent's personality or workflows

Edit `nanobot/skills/traverz/SKILL.md` — the agent reloads it on restart. No code changes needed for instruction-level adjustments.

### Deploy to Cloud Run

```bash
./deploy.sh
```

Or via Cloud Build (pipeline defined in `cloudbuild.yaml`):

```bash
gcloud builds submit --config=cloudbuild.yaml .
```

---

## 12. Troubleshooting

| Symptom | Likely Cause |
|---|---|
| `FileNotFoundError: Config not found` | No config at `~/.traverz/config.json`. Run `mkdir -p ~/.traverz && cp traverz_config.json ~/.traverz/config.json` |
| `No API key configured for provider 'gemini'` | `GEMINI_API_KEY` env var not set, or config has unresolved `${GEMINI_API_KEY}` placeholder |
| WebSocket connection refused | Bot not running in gateway mode. Start with `traverz gateway` |
| `No user JWT available` | WebSocket client didn't send auth token, or token validation failed |
| `You have 'viewer' access` | The user's role in the trip is viewer — write tools require owner/editor |
| gcsfuse mount failure | `--privileged` missing in Docker, or GCS credentials not configured |
| "did not receive a valid HTTP request" in logs | Harmless — Cloud Run TCP health probe. Filtered in `websocket.py` |
