from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from . import db, llm, exporter
from .config import settings
from .services import intake, mediation, custody, predict, dispute

api = APIRouter()
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIMES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp"}


def _need(rec, what="record"):
    if not rec:
        raise HTTPException(404, f"{what} not found")
    return rec


@api.get("/health")
def health():
    return {"ok": True, "ai_mode": llm.mode(), "model": settings.GEMINI_MODEL, "last_llm_error": llm.LAST_ERROR, "usage": llm.usage(),
            "doc_types": intake.DOC_TYPES, "disclaimer": settings.DISCLAIMER}


# ============================ Module 1 ============================
@api.post("/cases")
def create_case(body: dict = Body(...)):
    facts = body.get("facts", {})
    cid = db.put("case", {"title": body.get("title", "Untitled case"), "facts": facts})
    return db.get(cid)


@api.get("/cases")
def list_cases():
    return db.list_("case")


@api.get("/cases/{cid}")
def get_case(cid: str):
    c = _need(db.get(cid, "case"), "case")
    c["derived"] = intake.derived(c["facts"])
    return c


@api.patch("/cases/{cid}/facts")
def patch_facts(cid: str, facts: dict = Body(...)):
    c = _need(db.get(cid, "case"), "case")
    return db.update(cid, {"facts": intake.merge_facts(c["facts"], facts, override=True)})


@api.post("/cases/{cid}/documents")
async def upload_document(cid: str, file: UploadFile = File(...)):
    c = _need(db.get(cid, "case"), "case")
    data = await file.read()
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower()
    text, files = "", None
    if ext in (".txt", ".md", ".csv"):
        text = data.decode("utf-8", "ignore")
    elif ext == ".docx":
        import io
        from docx import Document
        text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    elif ext in MIMES:  # Gemini does the OCR / parsing natively
        files = [(MIMES[ext], data)]
    else:
        raise HTTPException(415, "Upload pdf, png, jpg, webp, docx, txt")
    extracted = await run_in_threadpool(intake.extract_entities, text, files)
    did = db.put("document", {"filename": file.filename, "extracted": extracted, "chars": len(text)}, cid)
    facts = intake.merge_facts(c["facts"], extracted, override=False)
    db.update(cid, {"facts": facts})
    return {"document_id": did, "extracted": extracted, "facts": facts, "ai_mode": llm.mode()}


@api.post("/cases/{cid}/research")
def research(cid: str, body: dict = Body(default={})):
    from . import knowledge
    c = _need(db.get(cid, "case"), "case")
    q = body.get("query") or c["facts"].get("dispute_summary") or "divorce maintenance custody"
    hits = knowledge.search(q, 6)
    note = ("Starter KB only. eCourts/Manupatra/SCC Online need licensed access - plug a connector into knowledge.search().")
    return {"query": q, "results": hits, "note": note}


@api.post("/cases/{cid}/drafts")
async def make_draft(cid: str, body: dict = Body(...)):
    c = _need(db.get(cid, "case"), "case")
    try:
        d = await run_in_threadpool(intake.generate_draft, c["facts"], body.get("doc_type", "divorce_13b"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    d["id"] = db.put("draft", d, cid)
    return d


@api.get("/cases/{cid}/drafts")
def list_drafts(cid: str):
    return db.list_("draft", cid)


@api.patch("/drafts/{did}")
def review_draft(did: str, body: dict = Body(...)):
    _need(db.get(did, "draft"), "draft")
    allowed = {k: v for k, v in body.items() if k in ("status", "text", "reviewer_notes")}
    if allowed.get("status") not in (None, "pending_review", "changes_requested", "approved"):
        raise HTTPException(400, "bad status")
    return db.update(did, allowed)


@api.get("/drafts/{did}/export")
def export_draft(did: str):
    d = _need(db.get(did, "draft"), "draft")
    label = "APPROVED by advocate" if d["status"] == "approved" else "DRAFT - NOT YET REVIEWED"
    return Response(exporter.to_docx(d["title"], d["text"], label), media_type=DOCX,
                    headers={"Content-Disposition": f'attachment; filename="{d["doc_type"]}.docx"'})


# ============================ Module 2 ============================
@api.post("/mediation/analyze")
async def med_analyze(body: dict = Body(...)):
    return await run_in_threadpool(mediation.analyze, body.get("party", "A"), body["text"], body.get("context", ""))


@api.post("/mediation/sessions")
def med_session(body: dict = Body(default={})):
    sid = db.put("med_session", {"issues": body.get("issues", []), "parties": body.get("parties", {"A": "Party A", "B": "Party B"})},
                 body.get("case_id"))
    return {"session_id": sid}


@api.post("/mediation/sessions/{sid}/message")
async def med_message(sid: str, body: dict = Body(...)):
    s = _need(db.get(sid, "med_session"), "session")
    a = await run_in_threadpool(mediation.analyze, body["party"], body["text"], str(s.get("issues")))
    db.put("med_msg", {"party": body["party"], "text": body["text"], "analysis": a}, sid)
    return {"analysis": a}


@api.get("/mediation/sessions/{sid}/messages")
def med_messages(sid: str):
    return db.list_("med_msg", sid)


@api.post("/mediation/sessions/{sid}/options")
async def med_options(sid: str):
    s = _need(db.get(sid, "med_session"), "session")
    return await run_in_threadpool(mediation.settlement_options, s.get("issues"), db.list_("med_msg", sid))


ROOMS: dict[str, list] = {}


@api.websocket("/ws/mediation/{sid}")
async def med_ws(ws: WebSocket, sid: str, role: str = Query("party"), party: str = Query("A")):
    """role=mediator sockets receive analysis. Party sockets only see the shared chat (never the analysis)."""
    await ws.accept()
    ROOMS.setdefault(sid, []).append((ws, role))
    try:
        while True:
            data = await ws.receive_json()
            text = (data.get("text") or "").strip()
            if not text:
                continue
            who = data.get("party", party) if role == "party" else "Mediator"
            shared = {"type": "message", "party": who, "text": text}
            for sock, _ in list(ROOMS.get(sid, [])):
                await sock.send_json(shared)
            if role == "party":
                a = await run_in_threadpool(mediation.analyze, who, text, "")
                db.put("med_msg", {"party": who, "text": text, "analysis": a}, sid)
                for sock, r in list(ROOMS.get(sid, [])):
                    if r == "mediator":
                        await sock.send_json({"type": "analysis", "party": who, "text": text, "analysis": a})
    except WebSocketDisconnect:
        pass
    finally:
        ROOMS[sid] = [(s, r) for s, r in ROOMS.get(sid, []) if s is not ws]


# ============================ Module 3 ============================
@api.post("/custody/plan")
async def custody_plan(body: dict = Body(...)):
    try:
        plan = await run_in_threadpool(custody.build_plan, body)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, f"bad input: {e}")
    plan["id"] = db.put("custody_plan", {"input": body, "plan": plan}, body.get("case_id"))
    return plan


@api.get("/custody/{pid}/export")
def custody_export(pid: str):
    r = _need(db.get(pid, "custody_plan"), "plan")
    return Response(exporter.to_docx("Parenting Plan", r["plan"]["agreement_text"]), media_type=DOCX,
                    headers={"Content-Disposition": 'attachment; filename="parenting_plan.docx"'})


# ============================ Module 4 ============================
@api.post("/analytics/predict")
async def a_predict(body: dict = Body(...)):
    return await run_in_threadpool(predict.predict, body)


@api.post("/analytics/tradeoff")
def a_tradeoff(body: dict = Body(...)):
    return predict.tradeoff(body)


@api.post("/analytics/disclosure-check")
def a_disclosure(body: dict = Body(...)):
    return predict.disclosure_check(body)


# ============================ Module 5 ============================
@api.post("/dispute/triage")
async def d_triage(body: dict = Body(...)):
    return await run_in_threadpool(dispute.triage, body["text"], body.get("facts"))


@api.post("/dispute/negotiations")
def neg_create(body: dict = Body(...)):
    neg = {"issues": body["issues"], "bids": {}, "round": 1, "results": None}
    nid = db.put("negotiation", neg, body.get("case_id"))
    return {"negotiation_id": nid, **neg}


@api.post("/dispute/negotiations/{nid}/bid")
def neg_bid(nid: str, body: dict = Body(...)):
    neg = _need(db.get(nid, "negotiation"), "negotiation")
    party = body.get("party")
    if party not in ("A", "B"):
        raise HTTPException(400, "party must be A or B")
    neg["bids"][party] = {"bids": body["bids"], "weights": body.get("weights", {})}
    results = dispute.resolve(neg["issues"], neg["bids"]) if len(neg["bids"]) == 2 else None
    db.update(nid, {"bids": neg["bids"], "results": results})
    neg = db.get(nid)
    return dispute.party_view(neg, party)


@api.get("/dispute/negotiations/{nid}")
def neg_view(nid: str, party: str = Query(...)):
    return dispute.party_view(_need(db.get(nid, "negotiation"), "negotiation"), party)


@api.post("/dispute/negotiations/{nid}/next-round")
def neg_next(nid: str):
    neg = _need(db.get(nid, "negotiation"), "negotiation")
    return dispute.party_view(db.update(nid, {"bids": {}, "results": None, "round": neg["round"] + 1}), "A") | {"your_bids": None}


@api.post("/dispute/negotiations/{nid}/agreement")
async def neg_agreement(nid: str, body: dict = Body(default={})):
    neg = _need(db.get(nid, "negotiation"), "negotiation")
    terms = dispute.agreed_terms(neg)
    if not terms:
        raise HTTPException(400, "No agreed issues yet. Submit both parties' bids and confirm at least one issue is agreed before drafting an agreement.")
    facts = {}
    if neg.get("case_id"):
        facts = (db.get(neg["case_id"], "case") or {}).get("facts", {})
    facts = {**facts, "settlement_terms": terms}
    d = await run_in_threadpool(intake.generate_draft, facts, "settlement_agreement")
    d["id"] = db.put("draft", d, neg.get("case_id"))
    return d


@api.post("/cases/{cid}/evidence")
async def add_evidence(cid: str, kind: str = Form("message"), date: str = Form(""), source: str = Form(""),
                       text: str = Form(""), file: UploadFile | None = File(None)):
    _need(db.get(cid, "case"), "case")
    item = {"kind": kind, "date": date, "source": source, "text": text}
    if file is not None:
        data = await file.read()
        ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower()
        if ext in MIMES:
            item["_files"] = [(MIMES[ext], data)]
        else:
            item["text"] = (text + "\n" + data.decode("utf-8", "ignore")).strip()
        item["source"] = source or file.filename
    tagged = await run_in_threadpool(dispute.tag_evidence, item)
    item.pop("_files", None)
    item.update(tagged)
    item["id"] = db.put("evidence", item, cid)
    return item


@api.get("/cases/{cid}/evidence/bundle")
def ev_bundle(cid: str):
    return dispute.bundle(db.list_("evidence", cid))


@api.get("/cases/{cid}/evidence/bundle/export")
def ev_export(cid: str):
    rows = dispute.bundle(db.list_("evidence", cid))
    body = "\n".join(f"## {r['exhibit']} - {r['date'] or 'undated'} ({r['kind']})\nTags: {', '.join(r['tags'] or [])} | Relevance: {r['relevance']}/5\n{r['summary']}\n"
                     for r in rows)
    return Response(exporter.to_docx("Evidence Bundle - Index of Exhibits", body), media_type=DOCX,
                    headers={"Content-Disposition": 'attachment; filename="evidence_bundle.docx"'})


@api.post("/cases/{cid}/hearing-brief")
async def hearing_brief(cid: str, body: dict = Body(default={})):
    c = _need(db.get(cid, "case"), "case")
    rows = dispute.bundle(db.list_("evidence", cid))
    return await run_in_threadpool(dispute.hearing_brief, c["facts"], rows, body.get("role", "petitioner"))
