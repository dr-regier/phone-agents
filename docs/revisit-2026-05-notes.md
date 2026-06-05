# Leo Revisit - Session Notes & Plan (2026-05-29)

> Picking the project back up after ~2 months away (last tested March 21). This doc
> captures the full baseline verification, the live-call trace diagnosis, the operational
> gotchas, and the plan ahead. Strategic context: this is FDE/Cortex-fluency practice -
> trace-driven latency + prompt/context debugging on a live agent (see `ryan-hq/ideal-role.md`).

---

## ▶ START HERE NEXT SESSION (last worked: Jun 5 2026)

**Done & shipped (on `main`, pushed to `backup`):**
- ✅ Repetition bug fixed + validated in-call (commit `b94dbcf`). See Jun 1 update below.
- ✅ **Answer-latency (18-35s dead air) FIXED + validated 5/5 calls (commit `24c425c`).** It was NOT
  ngrok/Twilio (3-session theory DISPROVEN) - it was fastrtc emit-queue starvation. Producer
  `_emit_to_queue` spun on `emit()==None`, flooding an unbounded queue (164k items) and starving the
  consumer from sending the greeting (sat at queue head from +0.1s but not pulled for 18.6s). Fix:
  20ms idle backoff + skip the None enqueue, as a monkeypatch in `observability/emit_instrument.py`
  installed from `api/main.py`. EMIT #0 `pre-send t+` dropped 18.62s -> 0.13-0.40s, qsize -> 0.
  Remaining ~3s = Twilio "one moment please" + stream setup (irreducible). Full convict in the Jun 5
  update below + memory `leo-answer-latency-in-transport`.
- ✅ Gradio browser-test fix (`run_gradio_application.py` `set_voice` config drift). Branches cleaned
  up - `main` is now the only branch (local + `backup`).

**🎯 START HERE NEXT SESSION - pick one (all genuinely open, none blocked):**
1. **Report the fastrtc emit-queue starvation bug upstream** (fastrtc 0.0.33). Real library concurrency
   bug; clean OSS contribution + portfolio story. The fix + root cause are already written in
   `observability/emit_instrument.py` - reuse that text for the issue/PR.
2. **Fix the `time_limit` 400 side bug.** `handle_incoming_call` fires the Twilio `time_limit` REST
   update before the call is in-progress, so it 400s ("Call is not in-progress") on every call. Off
   the latency path but dead as written - move it later / retry once the call is live.
3. **Per-turn latency** (the other dead air, mid-conversation): TTS variance 1.7-12s is the main one;
   property search regressed to 5-14s (was ~4s); occasional ~6.8s LLM spike (try `reasoning_effort="low"`).
4. **Parked: voice-replay eval harness** - batch-test STT->LLM->TTS without placing calls. Now MORE
   attractive since the answer-latency bug (which it couldn't reproduce) is solved. See Jun 2 update.

**To get running again:** `DOCKER_API_VERSION=1.41 docker compose up -d` (NOT `--build`, it hangs),
wait for `/health`, then POST `/superlinked/ingest`. For phone calls: `make start-ngrok-tunnel` +
point Twilio webhook at the tunnel. Browser latency test: `make start-gradio-application`
(whisper-groq + together + Leo). The emit-queue fix loads automatically via `api/main.py`.

Everything below this line is the older (pre-Jun-5) plan, kept for context.

---

**🎯 TOP PRIORITY: chase call-answer latency (18-35s) via cheap bisection.**
This is the biggest user-facing issue. The discipline: *localize the seconds to one hop
before instrumenting or tuning anything.* Run the diagnostics cheapest-decisive first -
do NOT jump to instrumenting code. Full reasoning in the Jun 2 update below.

Run in this order, each one eliminates a hop:
1. **Browser A/B test (do this FIRST - most decisive single cut).** FastRTC's built-in
   WebRTC UI bypasses BOTH ngrok and Twilio. Greet Leo in-browser vs on the phone:
   - Instant in browser → problem is ngrok or Twilio, NOT our app. Stop reading our code.
   - Also laggy in browser → problem is FastRTC emit / our side. Then step 4 is worth it.
   Next action when we resume: check the FastRTC browser UI is reachable + how Leo launches.
2. **ngrok inspector (`http://127.0.0.1:4040`) - free, zero code, already running.**
   Shows per-connection tunnel timing incl. the media-stream WebSocket. Data we've never read.
3. **Twilio Console call logs - free, server-side timestamps** of stream events
   (connected/start/media) = the far end of the pipe's view of when audio actually arrived.
4. **Instrument fastrtc `_emit_loop`** (queue depth + per-chunk send timestamps) - ONLY if
   steps 1-3 point back inward at our app.

Why this order: steps 1-3 are ~zero effort and each eliminates a hop. We don't write a line
of instrumentation until the free data says which hop to instrument.

Leading hypothesis (NOT yet proven): the 2x call-to-call variance (18 vs 35s) + startup-only
pattern points at infra (ngrok free-tier throttling the high-freq media WS), not a fixed code
path. Treat as suspect, not verdict. Context: memory `leo-answer-latency-in-transport`.

**Then (lower priority): per-turn latency** - TTS variance (1.7-12s) is the main per-turn dead
air; property search regressed to 5-14s (was ~4s); occasional ~6.8s LLM spike (try
`reasoning_effort="low"`).

**Parked idea (raised Jun 2, not started):** build an eval harness to replay voice turns
WITHOUT placing phone calls (tap STT→LLM→TTS below the transport) so we can batch-test changes
instead of manually calling 5x per change. Note: this helps per-turn/repetition testing but
canNOT reproduce the answer-latency bug - that one only lives in the real transport (the browser
A/B is its substitute). Revisit after answer-latency is localized.

**To get running again:** `DOCKER_API_VERSION=1.41 docker compose up -d` (NOT `--build`,
it hangs - existing image is fine), wait for `/health`, then POST `/superlinked/ingest`.
Trace tool: `/tmp/analyze_calls.py` (copy into container, run with `/app/.venv/bin/python`).

---

## TL;DR

- **Baseline is healthy.** Full pipeline verified end to end, including a real phone call
  with two SMS-confirmed showings booked in one call.
- **Two real issues found via Opik traces**, both with clean root causes and cheap fixes:
  1. End-of-call **repetition/run-on** = uncapped LLM generation (`ChatGroq` has no `max_tokens`).
  2. **3-4s dead air** before each response = **STT is ~2s every turn** (biggest fixed cost).
- **Plan:** cap the LLM + swap STT to a faster model, re-test the same call, compare traces.
  Open tradeoffs to decide first (see Plan section).

---

## UPDATE - Jun 1 2026 session

Made 8 more test calls and reviewed logs + Opik traces before changing anything. Outcomes:

### Corrections to the notes above
- **Runtime model is `openai/gpt-oss-120b`** (set in `.env` via `GROQ__MODEL`), not `20b`.
  `config.py`'s default is still `20b` but `.env` overrides it.
- **gpt-oss is a reasoning model** - reasoning tokens count toward `max_tokens`. So the
  original "set `max_tokens=150`" idea was a latent bug: it would truncate real answers
  (a full property readout ~130 tokens + reasoning ~150 > 150). max_tokens is a weak
  anti-ramble lever anyway - the bad outputs (~100 tokens) aren't much longer than good ones.

### Repetition - CONFIRMED persistent, then FIXED
Appeared in at least 3 of 5 conversational calls, always on **scheduling/confirmation turns**.
Two flavors, same root cause (uncapped generation): literal restatements, and the worse
"role-play" mode where the model writes BOTH sides of several future turns in one reply
("...works for you?Sounds good. Any must-have features?..."). The `?Word` joins (no space)
dodge the splitter and play as one 10-12s breath = repetition and worst-latency are one bug.

**Fix applied** (`fastrtc_agent.py`):
- LLM: `frequency_penalty=0.6` (primary lever, via `model_kwargs`), `temperature=0.5`,
  `max_tokens=512` (runaway backstop only - high so it never truncates answer+reasoning).
- `_split_sentences`: now also splits `?`/`!` immediately followed by a capital, so a run-on
  can't play as one breath. Deliberately does NOT split a bare `.` (protects "p.m."/decimals).
- Verified Groq accepts `frequency_penalty` on this model via a live invoke. **Still needs a
  before/after call batch to confirm in-call** (use `/tmp/analyze_calls.py`, trace-only REP/SLOW).

### Per-turn latency - re-measured
STT improved to ~0.5-1.7s (no longer the bottleneck). LLM usually <2s, occasional ~6.8s spike
(`reasoning_effort="low"` is a candidate lever). TTS highly variable 1.7-12s (main per-turn
dead air now). **Property search REGRESSED to 5-14s** (was ~4s) - worth a look, but it's behind
the "let me check" preamble so not pure dead air.

### NEW headline issue - call-ANSWER latency (18-35s) is in the transport, not Leo
Caller hears Twilio's "one moment please" at ~2s, then 18-35s of dead air before Leo's greeting.
Instrumented the connect path (⏱️ logs): agent rebuild = 0.04-0.09s; greeting is **emitted
~0.1s after the WebSocket opens**, every call (launched on Twilio's `start` event with
`stream_id` set, so not dropped). So **all the dead air is downstream of our `yield`**, in the
FastRTC→ngrok→Twilio path, and it's **startup-only** (conversations flow fine after). FastRTC's
`_emit_loop` paces phone audio at 0.75x realtime, so a 4.4s greeting should ship in ~3.3s - the
18-35s + its variance is unexplained by static code. Two unseparated suspects: (1) emit queue
backing up at startup, (2) ngrok free-tier throttling the media-stream WebSocket.
**No code change to Leo will fix this** (already 0.1s). Next diagnostic (unstarted): instrument
fastrtc `_emit_loop` (queue depth + send timestamps), OR browser A/B via FastRTC's WebRTC test
UI (bypasses Twilio + ngrok) to convict/exonerate ngrok.

### Tooling notes (this session)
- `/tmp/analyze_calls.py` - trace-only call analysis (newest N calls, per-turn total + REP/SLOW
  flags, no per-turn span queries so it dodges Opik's span rate limit). Run inside the container:
  `docker compose cp` it in, then `/app/.venv/bin/python /app/analyze_calls.py 6`.
- Source is bind-mounted (`./src/realtime_phone_agents:/app/...`), so code edits need only
  `docker compose restart phone-calling-agent-api` - no rebuild. `--build` was hanging (compose
  deadlock in `futex_`); the existing image already has all committed code.

---

## UPDATE - Jun 2 2026 session (planning, no code changes)

Mostly a thinking/planning session. Sharpened the answer-latency attack into a concrete
bisection plan and parked an eval-harness idea. No calls placed, nothing shipped.

### Decision: answer-latency is the top priority, chased by cheap bisection
The whole span between our `yield` and the caller's ear is unmeasured. We've only proven
the greeting is **yielded** at 0.1s; the 18-35s hides across three un-instrumented hops:

```
yield (our code) ──► FastRTC _emit_loop sends on WS ──► ngrok tunnel ──► Twilio buffers + plays ──► ear
  0.1s ✓ measured        ✗ unmeasured              ✗ unmeasured       ✗ unmeasured
```

The job is to **bisect that span** until the seconds are cornered in one hop. Two signals
drive the hypothesis: (1) **startup-only** = it's connection-setup, not bandwidth/codec;
(2) **2x call-to-call variance (18 vs 35s)** = smells like infra/queue contention, not a
fixed code path → ngrok free-tier is the leading suspect, but that's a hypothesis to test,
not a verdict. The ranked diagnostic plan (browser A/B → ngrok inspector → Twilio logs →
instrument `_emit_loop` only if needed) is now written in the START HERE block at the top.

### Parked: voice-replay eval harness
Ryan raised the real pain - manually placing 5 calls per change to verify is slow. Idea:
replay turns below the transport (drive STT→LLM→TTS directly) to batch-test without phoning.
Was about to map the agent's seams (`fastrtc_agent.py`, the react agent, STT/TTS interfaces,
existing tests) when we pivoted to answer-latency. Key caveat captured: this harness helps
per-turn + repetition testing but **cannot reproduce the answer-latency bug** (that lives only
in the real transport; the browser A/B is its stand-in). Revisit after answer-latency is localized.

### Methodology notes worth keeping (AI-engineer muscle, for Five9 Cortex/FDE)
- **Localize before you tune.** Attribute every problem to a pipeline stage (STT/LLM/TTS/
  transport) before touching a knob. Half our findings were NOT prompt problems.
- **A stage you didn't timestamp is a stage you'll wrongly blame.** Instrument boundaries, diff them.
- **Median vs tail.** A spread (TTS 1.7-12s) IS the finding; tails mean queueing/retries/cold paths.
- **Flag + trigger.** A flagged anomaly is noise until grouped by what input produced it.
- **One variable at a time.** The repetition fix changed 3 knobs at once - `frequency_penalty`
  is the *suspected* primary lever, NOT verified in isolation. Don't over-claim cause.

---

## Baseline verification (all ✅)

Verified May 29, 2026 in the **local docker** mode (the way Leo is actually run):

| Component | Status | Notes |
|-----------|--------|-------|
| Python env / imports | ✅ | `uv` builds clean, 333 pkgs, no resolution conflict |
| Groq LLM | ✅ | `ChatGroq.invoke`, model `openai/gpt-oss-120b` (set in `.env`; `config.py` default is `20b`) |
| Groq STT | ✅ | `whisper-large-v3` reachable |
| Together AI TTS | ✅ | `/audio/speech`, 8kHz native (Cartesia Sonic 3) |
| Twilio | ✅ | Account active, number still owned |
| Local docker stack | ✅ | Builds + boots, health endpoint green |
| Qdrant (local) + ingest | ✅ | 60 properties from `data/properties.csv` |
| Property search end-to-end | ✅ | Returns relevant Denver listings |
| Opik tracing | ✅ | Workspace `dr-regier`, project `phone-agents` |
| **Live phone call** | ✅ | Booked 2 showings, 2 distinct SMS received |

---

## Operational gotchas (learned this session)

1. **Docker API version mismatch.** The `docker compose` plugin negotiates API v1.52, but
   the daemon on this machine (Docker 20.10.24) only supports up to **1.41**. Plain `docker`
   works; `docker compose` fails with `client version 1.52 is too new`.
   **Fix:** prefix with `DOCKER_API_VERSION=1.41`. The Makefile targets do NOT set this.

2. **Local docker does not auto-ingest.** `make start-call-center` starts an empty local
   Qdrant. You must populate it after the app is healthy.
   Correct local startup sequence:
   ```bash
   DOCKER_API_VERSION=1.41 docker compose up --build -d
   # wait for health (app loads embedding model on boot, ~1-2 min):
   curl http://localhost:8000/health
   # ingest into the local Qdrant:
   curl -X POST http://localhost:8000/superlinked/ingest \
     -H "Content-Type: application/json" \
     -d '{"data_path": "/app/data/properties.csv"}'
   # verify:
   curl -X POST http://localhost:8000/superlinked/search \
     -H "Content-Type: application/json" \
     -d '{"query": "3 bedroom in Denver under 700k", "limit": 2}'
   # for inbound calls:
   make start-ngrok-tunnel   # then point the Twilio number's webhook at the tunnel
   ```
   The `qdrant_data` volume persists across `docker compose down`, so a restart does NOT
   need re-ingest (only `down -v` wipes it).

3. **Two DB modes - don't confuse them.**
   - **Local docker** (what Ryan uses): `docker-compose.yml` overrides `.env` with
     `QDRANT__USE_QDRANT_CLOUD=false`, local Qdrant container. Healthy.
   - **Cloud / gradio** (`make start-gradio-application`, runs on host, reads `.env`,
     `USE_QDRANT_CLOUD=true`): the Qdrant **Cloud** `default` collection is **stale** - its
     index config no longer matches Superlinked 37.5.0 (version drift), so it silently falls
     back to an empty in-memory store and returns no results. Only matters if switching to
     cloud mode; would require recreating the collection (`override_existing`) + re-ingest.

---

## Live-call trace diagnosis (the real work)

Pulled the Opik trace for the test call (thread `MZ902ef17...`, 13 turns). Two issues, both
confirmed from per-stage span timing.

### Issue 1 - Repetition / "hitch in the loop" (the headline bug)

On **turn 12** (caller asked to schedule the 2nd showing but gave no day/time), the model
produced ONE response containing five near-duplicate restatements of the same question:

```
"Got it. What day and time work for you to see the City Park bungalow? Morning or afternoon?
 ...Just let me know what fits your schedule, and I'll lock it in.
 ...Whenever you're ready—just give me a day and time that works for you.
 ...No rush—just hit me with what works and we'll get it on the calendar.
 ...I'm here whenever you're ready—just let me know the day and time..."
```

**Root cause:** `ChatGroq` (`src/realtime_phone_agents/agent/fastrtc_agent.py:144`) is created
with **no `max_tokens` and no `temperature`**. `gpt-oss-120b` degenerated into repetition and
nothing capped it. Secondary: the `?...` joins dodge the sentence splitter
(`_split_sentences`, regex `(?<=[.!?])\s+` needs a space after punctuation), so it all played
as one run-on instead of stopping.

**Knock-on effect:** this same turn had the worst latency - TTS took **11.68s** just to
synthesize all that text. So the repetition and the worst lag are the *same* bug.

### Issue 2 - Latency (3-4s dead air before Leo responds)

Per-stage span timing (dead air ≈ STT + LLM + first-sentence TTS):

| Turn | STT | LLM (ChatGroq) | TTS | Tool | Total |
|------|-----|----------------|-----|------|-------|
| 0 (simple chat) | 2.03s | 0.76s | 4.31s | - | 7.11s |
| 6 (property search) | 2.31s | 0.65s + 0.64s | 0.79s + 4.07s | search 4.00s | 12.56s |
| 12 (the ramble) | 2.20s | 1.51s | **11.68s** | - | 15.51s |

**Findings:**
- **STT is the dominant fixed cost: ~2.0-2.3s on EVERY turn.** The LLM is fast (~0.65s).
  STT is the #1 latency lever for perceived responsiveness.
- TTS (Together/Cartesia, `stream=False`) is ~4s for a normal response; the code already
  sentence-splits and yields per sentence, so first-audio latency = first sentence only.
- The **property search tool takes ~4.0s** (likely re-embedding the query on CPU each call),
  but it sits behind the "let me check" spoken preamble, so it's not dead air. Lower priority.

---

## Plan ahead

### Fix A - Cap the LLM (fixes repetition + worst latency)
> ⚠️ SUPERSEDED by the Jun 1 fix above. The `max_tokens=150` / `temperature=0.3` values below
> were the original guess; the applied fix uses `frequency_penalty` as the primary lever and a
> high `max_tokens=512` backstop (because gpt-oss is a reasoning model). Kept for history.

File: `src/realtime_phone_agents/agent/fastrtc_agent.py:144`
```python
llm = ChatGroq(
    model=settings.groq.model,
    api_key=settings.groq.api_key,
    max_tokens=150,      # hard ceiling so it can't ramble into restatements
    temperature=0.3,     # less likely to degenerate into repetition
)
```
**Open tradeoffs to decide first:**
- `max_tokens=150` might truncate legitimately longer responses (e.g. reading two property
  descriptions back to back). May need a higher cap, or the agent should summarize instead.
- `temperature=0.3` could make Leo sound more robotic, working against the personality the
  recent prompt rewrite added. Tune against that.
- Consider also hardening `_split_sentences` to handle `?...` / `...` joins so a run-on
  can't play as one breath even if the model rambles.

### Fix B - Faster STT (fixes general dead air)
Swap `whisper-large-v3` → `whisper-large-v3-turbo` on Groq (same provider, ~2-4x faster).
Config: `GROQ__STT_MODEL` in `.env` or the default in `config.py` (`GroqSettings.stt_model`).
The STT class (`stt/groq/whisper.py`) already reads `settings.groq.stt_model`, so it's a
config-only change.
**To verify first:** confirm `whisper-large-v3-turbo` is a valid Groq model id and check
accuracy on real call audio (turbo can be slightly less accurate).

### Test loop (the FDE workflow to repeat)
1. Make change on a branch (suggest `fix/latency-and-repetition`).
2. `DOCKER_API_VERSION=1.41 docker compose up --build -d`, wait for health (data persists,
   no re-ingest needed).
3. Re-test the SAME call script (incl. the double-booking that triggered the ramble).
4. Pull traces (`/tmp/pull_traces.py`, `/tmp/pull_spans.py` from this session - or rebuild
   them) and compare before/after per-stage timing. Capture the numbers - good demo material.

### Housekeeping still pending
- **Branch cleanup:** `week4-tts-experiment` is even with `main` (0 commits ahead). Also
  `week3`, `week4`, and a `backup` remote with copies. Consolidate to avoid guessing which
  branch is real. Confirm before deleting.

---

## Bigger picture (the two strategic build targets)

From `ideal-role.md`: the goal is FDE/AI-Engineer fluency, and Five9 Cortex = OpenAI brain +
ElevenLabs voices, with a day-to-day that's ~80% logs/traces/latency debugging.

1. **Latency/trace instrumentation** - exactly what this session did. The spans already exist;
   surfacing a per-stage latency view (or dashboard) would make this repeatable and demo-able.
2. **Un-shelve the OpenAI Realtime version** (`docs/openai-realtime-spec.md`) - mirrors the
   Cortex architecture. Shelved on cost ($2.50/call); reconsider for skill-building with a
   spend cap, since it rehearses the exact stack.
3. **(Optional) ElevenLabs TTS adapter** - matches Cortex's voice layer; the `tts/` layer is
   already pluggable.

The Leo fixes above are the immediate, high-energy on-ramp: a real observed bug, diagnosed
from traces, fixed and re-measured. That's the muscle.
