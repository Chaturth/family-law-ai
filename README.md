# Dispute Architect AI - Family Law AI Agent System (India)

Working model of the PRD: 5 modules, Python (FastAPI) backend, Gemini for all AI, single-page frontend (no build step).

## Run it (5 minutes)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then paste GEMINI_API_KEY when you have it
uvicorn app.main:app --reload --port 8000
```
Open http://localhost:8000 (UI) and http://localhost:8000/docs (interactive API docs).
Without a key it runs in **offline-demo** mode (rule-based fallbacks) - everything works, AI quality is basic.
Cap Gemini spend with `GEMINI_DAILY_LIMIT` (default 200 calls per UTC day); usage shows at `/api/health`.
Add the key, restart, header shows `AI: gemini`. Tests: `python -m unittest tests.test_services`.

## PRD module -> code map
| PRD module | Service | Key endpoints |
|---|---|---|
| 1 Intake, pleading, drafting | `services/intake.py`, `knowledge.py`, `exporter.py` | `POST /api/cases`, `/cases/{id}/documents` (OCR via Gemini), `/research`, `/drafts`, `PATCH /drafts/{id}` (human review), `GET /drafts/{id}/export` (.docx) |
| 2 Mediation copilot | `services/mediation.py` | WebSocket `/api/ws/mediation/{sid}?role=party\|mediator`, `/mediation/analyze`, `/sessions/{sid}/options` |
| 3 Custody modeler | `services/custody.py` | `POST /api/custody/plan`, `GET /custody/{id}/export` |
| 4 Predictive analytics | `services/predict.py` | `POST /api/analytics/predict`, `/tradeoff`, `/disclosure-check` |
| 5 Dispute architect | `services/dispute.py` | `/dispute/triage`, `/dispute/negotiations` (+`/bid`), `/cases/{id}/evidence`, `/evidence/bundle`, `/hearing-brief` |

## Design decisions (read before the demo)
1. **LLM drafts, rules decide safety.** Emergency triage (violence, child snatching), mediation threat detection and statute
   checks (e.g. s.14 HMA one-year bar) are rule-based floors the model cannot lower.
2. **Drafting = deterministic template + Gemini polish.** The model may not invent facts/citations; missing data stays `[●]`.
3. **Human-in-the-loop**: every draft starts `pending_review`; .docx export is watermarked until an advocate approves.
4. **Blind bidding never leaks** the other party's numbers; only per-issue outcome (agreed/gap band) is revealed.
5. **Privacy**: every record is Fernet-encrypted at rest (DPDP / Family Courts Act s.11). Add TLS, auth, audit log for production.

## Known gaps (be upfront with the team lead)
- **eCourts / Manupatra / SCC Online**: no open APIs. `knowledge.py` is a small curated starter KB marked *unverified*.
  Plug licensed access (or a vector store of judgments) into `knowledge.search()`.
- **Prediction is heuristic**, not trained: no public labelled Indian family-court outcomes dataset exists. Put real rows in
  `data/outcomes.csv` (see `outcomes.example.csv`, >=30 rows) and the kNN layer activates automatically. Heuristic bands in
  `predict.PARAMS` must be calibrated by a lawyer.
- No authentication/roles yet (litigant vs lawyer vs mediator), no live speech-to-text (feed transcripts into the WebSocket),
  no Hindi/regional-language UI (Gemini handles the language; UI strings are English).
- All legal citations/section numbers need review by a practising advocate before any real use.

## Roadmap (suggested sprints)
1. Auth + roles (JWT), Postgres, audit log. 2. Real research connector + judgment vector store.
3. Speech-to-text for live mediation. 4. Outcome dataset collection + proper model/evaluation.
5. Court-specific templates & eCourts formatting. 6. Security review, TLS, backups, DPDP consent flows.
