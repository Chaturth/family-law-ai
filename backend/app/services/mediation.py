"""Module 2 - AI Mediation Copilot. Analysis is shown ONLY to the mediator."""
import re
from .. import llm

HOSTILE = ["idiot", "stupid", "liar", "useless", "pathetic", "hate you", "shut up", "disgusting", "worthless",
           "crazy", "bastard", "nonsense", "coward"]
ULTIMATUM = r"\b(or else|last chance|take it or leave it|never agree|over my dead body|final offer|i will never|if you (?:don'?t|do not))\b"
THREAT = r"\b(kill|hurt you|beat you|finish you|destroy you|you'?ll regret|ruin you|take (?:the )?(?:kids|children) away)\b"
BLAME = r"\byou (?:always|never)\b"

SCHEMA = """Return JSON: {"sentiment": number -1..1, "emotion": string, "escalation": 0|1|2|3,
"flags": [personal_attack|ultimatum|threat|blame|stonewalling], "interests": [string], "positions": [string],
"non_negotiables": [string], "rephrase": string (neutral, interest-based, same language as input),
"mediator_prompt": string (one short suggestion for the mediator)}"""


def _fallback(party, text):
    t = text.lower()
    flags = []
    if any(w in t for w in HOSTILE):
        flags.append("personal_attack")
    if re.search(ULTIMATUM, t):
        flags.append("ultimatum")
    if re.search(THREAT, t):
        flags.append("threat")
    if re.search(BLAME, t):
        flags.append("blame")
    esc = 3 if "threat" in flags else min(2, len(flags))
    sent = max(-1.0, -0.35 * len(flags) - (0.2 if "!" in text else 0))
    reph = (f"Party {party} would like to be heard on this point. Their underlying concern seems to be about "
            f"fairness and security; can we restate it as what they need going forward?") if flags else text
    prompt = {0: "No intervention needed.", 1: "Monitor tone.", 2: "Consider a short caucus or reframe.",
              3: "Pause the session; address safety before continuing."}[esc]
    return {"sentiment": round(sent, 2), "emotion": "hostile" if flags else "neutral", "escalation": esc,
            "flags": flags, "interests": [], "positions": [], "non_negotiables": [],
            "rephrase": reph, "mediator_prompt": prompt}


def analyze(party, text, context=""):
    prompt = (f"Mediation context: {context}\nParty {party} just said:\n\"{text}\"\n\n{SCHEMA}\n"
              "Escalation 3 = threats/violence. Rephrase must keep the substance but drop blame and insults.")
    out = llm.json_(prompt, system="You are an expert, neutral family mediator in India (Mediation Act 2023).",
                    fallback=lambda: _fallback(party, text))
    base = _fallback(party, text)
    for k, v in base.items():
        out.setdefault(k, v)
    # Safety floor: rule-based threat detection can raise, never lower, the escalation level.
    if "threat" in base["flags"]:
        out["escalation"] = 3
    out["ai_mode"] = llm.mode()
    return out


def settlement_options(issues, messages):
    """Cross-reference both parties' interests and propose balanced packages."""
    interests = {"A": [], "B": []}
    for m in messages:
        for i in (m.get("analysis") or {}).get("interests", []):
            interests.setdefault(m.get("party", "?"), []).append(i)
    prompt = (f"Issues in dispute: {issues}\nParty A interests: {interests.get('A')}\nParty B interests: {interests.get('B')}\n"
              "Transcript excerpts:\n" + "\n".join(f"{m['party']}: {m['text']}" for m in messages[-25:]) +
              '\n\nPropose 3 balanced settlement frameworks. Return JSON {"options":[{"title","summary","gives_A",'
              '"gives_B","risks"}]}. Cover parenting schedule and financial payout where relevant.')

    def fb():
        return {"options": [
            {"title": "Phased arrangement", "summary": "Interim 6-month arrangement, then review with a neutral mediator.",
             "gives_A": "Stability now", "gives_B": "Guaranteed review date", "risks": "Needs good-faith compliance"},
            {"title": "Package trade-off", "summary": "Trade one high-priority issue for another (e.g. residence rights vs. lower monthly maintenance).",
             "gives_A": "Priority issue A", "gives_B": "Priority issue B", "risks": "Valuation disagreements"},
            {"title": "Neutral expert", "summary": "Independent valuer/child psychologist reports binding on the disputed item.",
             "gives_A": "Objectivity", "gives_B": "Objectivity", "risks": "Cost and delay"}]}
    out = llm.json_(prompt, system="You are a neutral mediator generating win-win options.", fallback=fb)
    out["ai_mode"] = llm.mode()
    return out
