"""Module 5 - Triage, blind bidding, evidence bundling, hearing brief."""
import re
from .. import llm

EMERGENCY = [r"\b(beat|beaten|hit|slap|assault|violen\w+|abus\w+|threat\w*|kill|acid|strangl\w+)\b",
             r"\b(snatch\w*|kidnap\w*|abduct\w*|took (?:the )?(?:child|kids|children))\b",
             r"\b(suicide|self[- ]harm|end my life)\b", r"\b(thrown out|locked out|evict\w+)\b",
             r"\b(dowry (?:demand|harass)\w*|cruelty)\b"]
HELP = {"police/emergency": "112", "women helpline": "181", "child helpline": "1098", "free legal aid (NALSA)": "15100"}


def triage(text, facts=None):
    hits = [p for p in EMERGENCY if re.search(p, text.lower())]
    rule_emergency = bool(hits)
    out = llm.json_(
        f"Dispute description:\n{text}\n\nAssess suitability for pre-litigation mediation under the Mediation Act, 2023. "
        'Return JSON {"route": "emergency_court"|"pre_litigation_mediation"|"direct_litigation", "urgency": 1-5, '
        '"reasons": [string], "recommended_steps": [string], "interim_reliefs": [string]}. Violence, child abduction or '
        "safety risk => emergency_court.",
        system="You are a cautious Indian family-law triage assistant.",
        fallback=lambda: {"route": "pre_litigation_mediation", "urgency": 2,
                          "reasons": ["No emergency indicators detected by rules."],
                          "recommended_steps": ["Send mediation notice", "Both parties file financial disclosure", "Start blind negotiation"],
                          "interim_reliefs": []})
    if rule_emergency:  # safety rules can escalate, never be overridden by the model
        out.update({"route": "emergency_court", "urgency": max(4, int(out.get("urgency", 4) or 4))})
        out.setdefault("reasons", []).insert(0, "Safety indicators detected - do not route to voluntary mediation first.")
        out["recommended_steps"] = ["Ensure immediate safety; call 112 if in danger",
                                    "Consult an advocate on Protection of Women from Domestic Violence Act, 2005 relief / interim custody orders",
                                    "Mediation only after protective orders and with safeguards"] + list(out.get("recommended_steps") or [])
        out["helplines"] = HELP
    out["rule_emergency"] = rule_emergency
    out["ai_mode"] = llm.mode()
    return out


# ------------------------------------------------------------ blind bidding
def resolve(issues, bids):
    """issues: [{key,label}]; bids: {"A": {"bids": {key:{ideal,reservation}}, "weights": {key:1-10}}, "B": ...}
    Reservation values are private; only the outcome per issue is revealed."""
    res = {}
    for it in issues:
        k = it["key"]
        a, b = bids["A"]["bids"].get(k), bids["B"]["bids"].get(k)
        if not a or not b:
            res[k] = {"status": "incomplete"}
            continue
        a_low = float(a["ideal"]) < float(a["reservation"])
        b_low = float(b["ideal"]) < float(b["reservation"])
        if a_low == b_low:
            res[k] = {"status": "invalid", "message": "Both parties want the same direction on this issue; re-check inputs."}
            continue
        low_p, high_p = ("A", "B") if a_low else ("B", "A")
        bid = {"A": a, "B": b}
        ceiling, floor = float(bid[low_p]["reservation"]), float(bid[high_p]["reservation"])  # max low-party pays, min high-party takes
        if floor <= ceiling:
            wl = float(bids[low_p].get("weights", {}).get(k, 5))
            wh = float(bids[high_p].get("weights", {}).get(k, 5))
            share_low = wl / (wl + wh)  # party who cares more gets the bigger part of the surplus
            val = ceiling - (ceiling - floor) * share_low
            res[k] = {"status": "agreed", "value": round(val, 2), "zone": [floor, ceiling]}
        else:
            gap = floor - ceiling
            pct = gap / max(abs(floor), 1) * 100
            res[k] = {"status": "gap", "gap_band": "small (<10%)" if pct < 10 else "moderate (10-30%)" if pct < 30 else "large (>30%)",
                      "message": "No overlap yet. Revise your reservation value or trade concessions across issues."}
    return res


def party_view(neg, party):
    """What a party may see: its own bids + the public outcome. Never the other side's numbers."""
    out = {"issues": neg["issues"], "round": neg["round"], "submitted": {p: p in neg["bids"] for p in ("A", "B")},
           "your_bids": neg["bids"].get(party), "results": neg.get("results")}
    return out


def agreed_terms(neg):
    lab = {i["key"]: i["label"] for i in neg["issues"]}
    return "\n".join(f"- {lab[k]}: {v['value']}" for k, v in (neg.get("results") or {}).items() if v["status"] == "agreed")


# ------------------------------------------------------------ evidence
TAGS = {"financial": r"\b(salary|bank|transfer|loan|payment|invoice|rs\.?|inr|rupees|emi)\b",
        "cruelty_threat": r"\b(threat\w*|abus\w+|beat|hit|violence|harass\w*)\b",
        "child_welfare": r"\b(child|son|daughter|school|custody|visit\w*)\b",
        "desertion_separation": r"\b(left home|moved out|separat\w+|desert\w*)\b",
        "dowry": r"\b(dowry|streedhan|stridhan|gifts?)\b"}


def tag_evidence(item):
    text = item.get("text", "")
    fb = lambda: {"tags": [t for t, p in TAGS.items() if re.search(p, text.lower())] or ["general"],
                  "relevance": 3, "summary": text[:160], "entities": []}
    out = llm.json_(
        f"Tag this evidence for an Indian family-court bundle. Source type: {item.get('kind')}, date: {item.get('date')}.\n"
        f'TEXT:\n{text}\n\nReturn JSON {{"tags":[financial|cruelty_threat|child_welfare|desertion_separation|dowry|income_proof|'
        f'general], "relevance":1-5, "summary":string<=40 words, "entities":[string]}}',
        system="You are a litigation paralegal.", files=item.get("_files"), fallback=fb)
    for k, v in fb().items():
        out.setdefault(k, v)
    return out


def bundle(items):
    ordered = sorted(items, key=lambda i: (i.get("date") or "9999", i.get("created", "")))
    return [{"exhibit": f"Ex. P-{n}", **{k: i.get(k) for k in ("date", "kind", "source", "tags", "relevance", "summary", "text")},
             "evidence_id": i.get("id")} for n, i in enumerate(ordered, 1)]


def hearing_brief(facts, bundle_rows, role="petitioner"):
    prompt = (f"Prepare a virtual family-court hearing brief for counsel for the {role}.\nFACTS: {facts}\nEXHIBITS: "
              f"{[{k: r[k] for k in ('exhibit', 'date', 'tags', 'summary')} for r in bundle_rows]}\n"
              'Return JSON {"argument_brief": string (<=300 words), "cross_examination_pointers": [{"topic","questions":[string],'
              '"exhibit_refs":[string]}], "weak_points": [string], "documents_to_prepare": [string]}. '
              "Cite only exhibits listed; invent nothing.")

    def fb():
        by = {}
        for r in bundle_rows:
            for t in r["tags"] or ["general"]:
                by.setdefault(t, []).append(r)
        qs = {"financial": ["Please confirm the source of this payment.", "Was this disclosed in your affidavit of assets?"],
              "cruelty_threat": ["Did you send this message on this date?", "What was the context?"],
              "child_welfare": ["Who handles the child's schooling and medical needs day to day?"],
              "general": ["Please identify this document and its author."]}
        return {"argument_brief": "Draft the brief from the chronological exhibits; counsel to complete.",
                "cross_examination_pointers": [{"topic": t, "questions": qs.get(t, qs["general"]),
                                                "exhibit_refs": [r["exhibit"] for r in rs]} for t, rs in by.items()],
                "weak_points": ["Gaps in chronology", "Unauthenticated screenshots (need s.63 BSA / s.65B Evidence Act certificate)"],
                "documents_to_prepare": ["Index of exhibits", "Certificate for electronic records", "Affidavit of assets"]}
    out = llm.json_(prompt, system="You are a senior Indian family-law litigator's assistant.", fallback=fb)
    out["ai_mode"] = llm.mode()
    return out
