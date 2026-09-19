# Dhvani: emergency command UI

The operator console for our voice-first emergency agent. It connects to the FastAPI backend in this repo and shows
what the AI dispatcher does, live, while it does it. A call comes in, the agent geocodes the location, merges
duplicate reports, scores severity, dispatches the best unit and escalates if needed. Every one of those steps
appears on the map, in the Live Line panel and on the event Wire.

---

## 👋 Hand-off: read this first

You're getting this folder as a zip. **Your job is to get it running, then commit it to `main` so the whole team can
pull it and refine from there.** Nothing in here is committed yet.

### Objective

1. Drop this folder into the repo as `frontend/` (next to `app/`, `main.py`, `tests/`).
2. Install and run it against the backend, and confirm it works (checklist below).
3. Commit it to `main` and push. After that we all pull and iterate on it.

### Step 1: put the folder in the repo

```bash
cd <path-to>/AI_VOICE-FIRST_EMERGENCY_AGENT
git checkout main
git pull origin main
# unzip so that the result is  AI_VOICE-FIRST_EMERGENCY_AGENT/frontend/package.json
```

The zip deliberately contains **no** `node_modules/`, `dist/` or `.env`. You create those locally.

### Step 2: install

Needs **Node.js 20.19+** (22 or 24 recommended). Check with `node -v`.

```bash
cd frontend
npm i
cp .env.example .env        # Windows PowerShell:  Copy-Item .env.example .env
```

`frontend/.env` holds only non-secret settings:

| Variable | Default | Meaning |
|---|---|---|
| `VITE_BACKEND_URL` | `http://127.0.0.1:8000` | Where the FastAPI backend runs. The dev server proxies `/api` and `/ws` there. |
| `VITE_DASHBOARD_API_KEY` | *(empty)* | Only if the backend sets `DASHBOARD_API_KEY`. |

### Step 3: run the backend (repo root, second terminal)

```bash
python -m venv .venv            # Python 3.11 recommended (some pinned packages lack 3.13+/3.14 wheels)
# activate:  source .venv/bin/activate   |   Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

In the **root** `.env`, set `ENABLE_DEV_ENDPOINTS=true`. That turns on the "Run a drill" button, which replays
the demo scenarios with no phone call and no OpenAI/Twilio keys needed. Then:

```bash
python main.py                  # http://localhost:8000  (SQLite + synthetic fleet are created on first start)
```

> If port 8000 is busy, run `python -m uvicorn main:app --port 8010` and set `VITE_BACKEND_URL=http://127.0.0.1:8010`
> in `frontend/.env`.

### Step 4: run the UI

```bash
cd frontend
npm run dev                     # http://localhost:5173
```

### Step 5: check it works (2 minutes)

- [ ] The top bar shows **LINK · LIVE** (green), and **Units ready** shows a number out of `/30`.
- [ ] The dark map of Ahmedabad loads with unit markers (AMB, FIR, POL…). Tiles come from CARTO, so you need internet.
- [ ] **Run a drill → Highway crash**: the Live Line panel types the call and the agent-trace tiles light up. On the map,
      a radar sweep runs and dashed dispatch lines appear. The incident shows up in the queue as `2 callers`.
- [ ] Click the incident: the dossier opens with the severity gauge, reasons, units, transcript and tool-call audit trail.
- [ ] **Run a drill → Prank caller**: it lands in the **Review** tab. Click **Genuine** and it moves to Active.
- [ ] `npm test` passes and `npm run build` succeeds.

If any box fails, see *Troubleshooting* below before committing.

### Step 6: commit to main and push

```bash
cd ..                           # repo root
git status                      # must NOT list frontend/node_modules, frontend/dist or frontend/.env
git add frontend
git commit -m "Add Dhvani operator console (React + Vite frontend)"
git push origin main
```

What **should** be committed: `src/`, `index.html`, `package.json`, `package-lock.json`, `tsconfig.json`,
`vite.config.ts`, `.env.example`, `.gitignore`, `README.md`. `frontend/.gitignore` already excludes the rest.
Always commit `package-lock.json`, so everyone installs identical versions.

Once it's pushed, tell the team. Everyone else then runs `git pull` → `cd frontend` → `npm i` → `npm run dev`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Cannot find native binding` / `@rolldown/binding-…` error on `npm run dev` | npm optional-dependency bug. Delete `node_modules` (keep `package-lock.json`) and run `npm i` again. If it persists, delete both and run `npm i`. |
| Top bar says **LINKING** / **OFFLINE** | The backend isn't reachable at `VITE_BACKEND_URL`. Check `curl http://127.0.0.1:8000/ping`, then restart `npm run dev` after editing `.env`. |
| Drill shows "Drills are off on the backend" | Set `ENABLE_DEV_ENDPOINTS=true` in the **root** `.env` and restart the backend. |
| Map area stays black | No internet access to `basemaps.cartocdn.com`. Markers and panels still work. |
| Timestamps look hours off | They shouldn't: the UI treats the backend's offset-less times as UTC. Report it if they do. |

## Scripts

| Command | What |
|---|---|
| `npm run dev` | Dev server with hot reload; proxies `/api` and `/ws` to the backend |
| `npm run build` | Typecheck + production build to `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm test` | Vitest: live-store reducer and timestamp handling |
| `npm run typecheck` | TypeScript only |

## How the UI maps to the backend

| UI | Backend |
|---|---|
| Initial snapshot | `GET /api/state` |
| Everything live | `WS /ws/dashboard`, all 18 hub event types, reduced in `src/store/live.ts` |
| Incident dossier | `GET /api/incidents/{id}` |
| Review tab → Genuine / ✕ | `POST /api/incidents/{id}/review` |
| Mark resolved / Close | `POST /api/incidents/{id}/status` |
| Approve / Reject dispatch (backend `DISPATCH_MODE=approval`) | `POST /api/assignments/{id}/approve` · `/reject` |
| Run a drill | `POST /api/dev/demo/{a–e}?pace=` |

The backend replays its last 200 events to each new WebSocket. The UI uses events older than 4 s to rebuild
transcripts and the wire, but never replays their animations.

## Project layout

```
src/
  App.tsx                     shell: full-bleed map + floating console grid
  main.tsx                    entry
  styles/globals.css          design tokens (@theme), motion tokens, map marker styles, responsive layout
  lib/                        api client, WebSocket link, types, taxonomy (mirrors backend enums), formatting, motion
  store/live.ts               Zustand store: turns hub events into state + one-off map effects (+ tests)
  components/                 shared: CommandBar, SeverityRing, AnimatedNumber, Pill
  features/
    map/                      MapLibre map, sweeps, unit glide, dispatch links, legend
    incidents/                incident queue (Active / Review / Closed)
    live-call/                Live Line: waveform, captions, agent tool pipeline
    incident-detail/          dossier drawer
    wire/                     event strip
    scenarios/                drill launcher
```

## Design rules (keep these when refining)

- **Colour carries meaning.** Severity is the only loud colour: flare `#ff4a1c` (critical), ember (high),
  sodium (medium), sage (low). Ice blue is the AI agent's colour: units, tool calls and its voice. Use the tokens in
  `globals.css` (`text-flare`, `bg-ink-3`…), never raw hex values in components.
- **Type:** Bricolage Grotesque for UI and headings, IBM Plex Mono for numbers and telemetry, Instrument Serif italic
  only for the caller's own words.
- **Motion:** GSAP for orchestration (map, gauge, queue reordering, typed captions), CSS for hovers and loops.
  Animate only `transform`/`opacity`, and keep `prefers-reduced-motion` working.
- New backend event type? Add a case in `src/store/live.ts` → `apply()` and a test in `live.test.ts`.
