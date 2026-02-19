# Phone Agents - Claude Code Context

## Project Overview
Real-time voice agent system from the [Neural Maze course](https://github.com/neural-maze/realtime-phone-agents-course). Currently on **Week 3** (Improving STT and TTS Systems). Building a real estate call center with AI voice agents.

## Current State
- **Branch:** `week3`
- **STT:** Groq Whisper (`STT_MODEL=whisper-groq`) — default, no GPU needed
- **TTS:** Together AI Orpheus 3B (`TTS_MODEL=together`) — default, no GPU needed
- **LLM:** Groq `openai/gpt-oss-20b` for conversation
- **Search:** Superlinked + Qdrant for property search
- **Phone:** Twilio + ngrok for inbound/outbound calls
- **RunPod:** Account created with $10 credits, not yet used

## Git Remotes
- `origin` = `https://github.com/neural-maze/realtime-phone-agents-course.git` (course repo, READ ONLY — never push here)
- `backup` = `git@github.com:dr-regier/phone-agents.git` (Ryan's personal backup)
  - `main` branch = week 2 code with Denver customizations
  - Future branches for subsequent weeks

## Personalizations (To Be Re-Applied)
Week 2 customizations are documented in detail at:
`~/Career-Transition/projects/phone-agents/personalization-instructions.md`

Summary of customizations:
- Agent name: **Josie** (not Lisa)
- Company: **Mile High Apartment Finders** (not The Neural Maze)
- Location: **Denver, Colorado** neighborhoods (not Madrid)
- Properties: 20 Denver rental apartments, prices in USD/month (not Madrid purchase prices in euros)
- Greeting: Pre-generated audio greeting on call connect
- Hot reload: Docker volume mount + uvicorn `--reload`
- Denver property CSV backup: `~/Career-Transition/projects/phone-agents/denver-properties.csv`

**Do not apply personalizations until the base week 3 code is running and tested successfully.**

## Key Files
- `src/realtime_phone_agents/config.py` — all settings, env var mapping
- `src/realtime_phone_agents/agent/fastrtc_agent.py` — main agent logic
- `src/realtime_phone_agents/stt/` — STT implementations (groq, local, runpod)
- `src/realtime_phone_agents/tts/` — TTS implementations (togetherai, local, runpod)
- `src/realtime_phone_agents/infrastructure/superlinked/` — property search
- `data/properties.csv` — property data (will be replaced with Denver data later)
- `.env` — API keys and configuration (not tracked in git)
- `.env.example` — template for env vars

## Quick Start Commands
```bash
cd ~/Projects/phone-agents
source .venv/bin/activate
newgrp docker
export DOCKER_API_VERSION=1.41
make start-call-center                   # build + start containers
ngrok http 8000                          # then update Twilio with new URL
docker logs phone-calling-agent-api -f   # watch logs
```

## After Startup
```bash
# Ingest property data
curl -X POST http://localhost:8000/superlinked/ingest -H "Content-Type: application/json" -d '{"data_path": "/app/data/properties.csv"}'
# Health check
curl http://localhost:8000/health
```

## Detailed Notes
Ryan's learning notes, build log, and session docs are at:
`~/Career-Transition/projects/phone-agents/`
- `build-log.md` — chronological progress, problems solved, insights
- `session-3.md` — week 3 specific notes
- `personalization-instructions.md` — how to re-apply Denver customizations
- `denver-properties.csv` — custom property data

## Environment
- Platform: ChromeOS Linux (Crostini), 6GB RAM
- Cannot run local models (OOM) — must use cloud APIs
- Docker API version pinned to 1.41
- Python 3.11, uv package manager
