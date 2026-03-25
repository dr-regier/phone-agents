# Phone Agents - Leo Voice Agent

## Owner
Ryan Regier. Career changer who deployed production AI projects to transition into AI engineering.
Starting as Five9 Automation Engineer on April 6, 2026.
Long-term goal: independence where W2 employment is a choice, not a necessity.

## What This Project Is
Voice agent call center built from The Neural Maze phone-agents course. Ryan's avatar "Leo"
is a Denver real estate agent with a live Twilio phone number. Used as a portfolio piece
during career transition - the LinkedIn demo was a strong talking point in interviews.

Ryan implemented this by following the course structure. He didn't design the architecture
from scratch. All avatars (leo, dan, jess, leah, mia, tara, zac, zoe) are course-provided.

## Current State
- Leo runs locally on Ryan's machine (not cloud-deployed)
- Last tested March 21, 2026 - everything working well
- Only Leo is registered in the avatar registry

## Strategic Value
- **Five9 relevance:** The Automation Engineer role involves IVA configuration (Dialogflow CX,
  Five9 IVA Studio). Leo demonstrates hands-on voice AI experience with STT, TTS, agent
  orchestration, and telephony - directly transferable concepts.
- **Portfolio piece:** Working production voice agent, not a toy demo.
- **Skill development:** Each improvement deepens understanding of the voice AI stack.
- **Long-term:** Voice AI + contact center domain = Ryan's differentiator. Leo is the lab
  for building expertise beyond what the day job teaches.

## Tech Stack
- **Agent:** FastRTC + LangChain/LangGraph + Groq LLM
- **STT:** Groq Whisper (whisper-large-v3)
- **TTS:** Cartesia Sonic 3 via Together AI (8kHz native, non-streaming)
- **Search:** Superlinked + Qdrant vector DB
- **Telephony:** Twilio (inbound + outbound)
- **Observability:** Opik tracing
- **Deployment:** Docker + RunPod (GPU)
- **Python 3.11, uv, FastAPI, Pydantic**

## Key Paths
- Agent logic: `src/realtime_phone_agents/agent/fastrtc_agent.py`
- API + routes: `src/realtime_phone_agents/api/`
- Avatar definitions: `src/realtime_phone_agents/avatars/definitions/`
- STT/TTS implementations: `src/realtime_phone_agents/stt/` and `tts/`
- Config: `src/realtime_phone_agents/config.py`
- Deployment: `Makefile`, `docker-compose.yml`, `Dockerfile*`

## How to Evaluate "What to Build Next"
1. **Does it deepen voice AI expertise?** (STT, TTS, agent orchestration, telephony)
2. **Is it relevant to the Five9 role?** (IVA patterns, contact center workflows, automation)
3. **Could it become a portfolio talking point?** (demo-able, explainable improvement)
4. **Does it build toward independence?** (skills/features that could serve future products)
5. **Energy test:** Does this feel like building or like homework?

## Communication Preferences
- Direct, concise. Bullet points over paragraphs.
- Use "deployed" not "shipped". Use "implemented" not "architected".
- No em dashes. No puffery. No overselling what the project is.
- Ryan doesn't need hand-holding or over-explanation.

## Career Context Reference
For full career goals, financial plan, and long-term strategy:
`/home/rregier/ryan-hq/`
