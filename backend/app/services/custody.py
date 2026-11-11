"""Module 3 - Custody & visitation modeler. Deterministic scheduling + scoring; LLM only drafts the narrative."""
import datetime as dt
from .. import llm

PATTERNS = {
    "alt_weekends": "Primary home with one parent; other parent alternate weekends (Fri+Sat nights)",
    "alt_weekends_midweek": "Alternate weekends plus a weekly Wednesday overnight",
    "2-2-3": "2-2-3 rotation (Mon-Tue / Wed-Thu / alternating Fri-Sun)",
    "week_on_off": "Alternating weeks (switch on Monday)",
}
HANDOVERS_PER_14D = {"alt_weekends": 2, "alt_weekends_midweek": 6, "2-2-3": 4, "week_on_off": 2}


def _next_monday(d):
    return d + dt.timedelta(days=(7 - d.weekday()) % 7)


def assign(pattern, week, wd):
    """Return 'P' (primary) or 'S' (secondary) for the overnight on weekday wd (0=Mon) of week index `week`."""
    odd = week % 2 == 1
    if pattern == "alt_weekends":
        return "S" if (odd and wd in (4, 5)) else "P"
    if pattern == "alt_weekends_midweek":
        return "S" if ((odd and wd in (4, 5)) or wd == 2) else "P"
    if pattern == "2-2-3":
        if wd in (0, 1):
            return "P"
        if wd in (2, 3):
            return "S"
        return "S" if odd else "P"
    if pattern == "week_on_off":
        return "S" if odd else "P"
    raise ValueError(pattern)


def calendar(pattern, primary, start, weeks=8):
    start = _next_monday(start)
    other = "B" if primary == "A" else "A"
    days = []
    for i in range(weeks * 7):
        d = start + dt.timedelta(days=i)
        who = primary if assign(pattern, i // 7, d.weekday()) == "P" else other
        days.append({"date": d.isoformat(), "weekday": d.strftime("%a"), "with": who})
    return days


def _ideal_band(age):
    if age < 3:
        return (0.10, 0.25)
    if age < 6:
        return (0.20, 0.35)
    if age < 13:
        return (0.30, 0.50)
    return (0.35, 0.50)


def _score(pattern, inp, primary, age, dist, special):
    cal = calendar(pattern, primary, dt.date.today(), 2)
    other = "B" if primary == "A" else "A"
    share = sum(1 for d in cal if d["with"] == other) / len(cal)
    lo, hi = _ideal_band(age)
    gap = 0 if lo <= share <= hi else min(abs(share - lo), abs(share - hi))
    contact = max(0, 100 - gap * 400)
    young, school = age < 5, age >= 5
    infeasible = None
    midweek = pattern in ("2-2-3", "alt_weekends_midweek", "week_on_off")
    if school and dist > 20 and midweek:
        infeasible = "School-day handovers not workable at this distance"
    if age < 3 and pattern != "alt_weekends":
        infeasible = "Under-3s: keep to short, frequent contact with a stable primary home"
    stability = max(0, 100 - HANDOVERS_PER_14D[pattern] * (9 if (young or special) else 5))
    par = inp["parent_a" if other == "A" else "parent_b"]
    avail = par.get("available_days")
    if avail is None:
        avail_fit = 100
    else:
        ok = [d for d in cal if d["with"] == other]
        avail_fit = 100 * sum(1 for d in ok if dt.date.fromisoformat(d["date"]).weekday() in avail) / max(1, len(ok))
    total = 0.4 * contact + 0.35 * stability + 0.25 * avail_fit
    return {"pattern": pattern, "label": PATTERNS[pattern], "score": round(total, 1), "contact_fit": round(contact, 1),
            "stability": round(stability, 1), "availability_fit": round(avail_fit, 1),
            "secondary_share_pct": round(share * 100, 1), "feasible": infeasible is None, "infeasible_reason": infeasible}


def _primary(inp, age):
    p = inp.get("proposed_primary")
    if p in ("A", "B"):
        return p, "Primary parent as proposed by the parties."
    a, b = inp["parent_a"], inp["parent_b"]
    if age < 5:
        for k, par in (("A", a), ("B", b)):
            if (par.get("role") or "").lower() == "mother":
                return k, "Child under 5: custody ordinarily with the mother (s.6(a) HMGA, 1956) - a rebuttable presumption, subordinate to welfare."
    la, lb = len(a.get("available_days") or range(7)), len(b.get("available_days") or range(7))
    return ("A" if la >= lb else "B"), "Primary parent chosen on availability for school-day care."


def holidays(age, dist, primary):
    other = "B" if primary == "A" else "A"
    summer = 14 if age < 6 else 28 if dist < 100 else 21
    return [
        f"Diwali: alternate years (Parent {primary} in even years, Parent {other} in odd years).",
        f"Holi / other major festival: opposite of Diwali each year.",
        f"Summer vacation: Parent {other} for {summer} days"
        + (", in two blocks of up to 7 days each." if age < 6 else ", up to 14 consecutive days."),
        "Winter break: split into halves; Christmas/New Year alternate yearly.",
        "Child's birthday: spent in two parts so both parents celebrate.",
        "Parents' birthdays and Mother's/Father's Day: child is with that parent for a few hours.",
    ]


def clauses():
    return [
        "Step 1 - Direct discussion: raise the issue in writing; reply within 7 days.",
        "Step 2 - Mediation: if unresolved in 14 days, attend mediation (private mediator or court-annexed centre) under the Mediation Act, 2023.",
        "Step 3 - Court: if mediation fails within 30 days, either parent may apply to the Family Court. Welfare of the child prevails.",
        "Travel: domestic trips over 3 nights need 14 days' notice; international travel needs prior written consent of both parents or court permission.",
        "School/medical decisions: joint for major decisions (school change, surgery); day-to-day by the parent with the child.",
        "Right of first refusal: if a parent cannot care for the child for more than 4 hours overnight, offer the other parent first.",
        "Communication: daily video call window; no disparagement of the other parent; no questioning the child about the other household.",
    ]


def build_plan(inp):
    child = inp["child"]
    age = float(child.get("age", 0))
    dist = float(inp.get("distance_km", 0))
    special = bool(child.get("special_needs"))
    primary, why = _primary(inp, age)
    cands = [_score(p, inp, primary, age, dist, special) for p in PATTERNS]
    feasible = [c for c in cands if c["feasible"]] or cands
    wanted = inp.get("preferred_pattern")
    chosen = next((c for c in feasible if c["pattern"] == wanted), None)
    note = None
    if wanted and not chosen:
        note = f"Requested pattern '{wanted}' not feasible/recommended here; best alternative selected."
    chosen = chosen or max(feasible, key=lambda c: c["score"])
    welfare = ["Welfare of the child is the paramount consideration (s.17 GWA 1890; s.13 HMGA 1956; Gaurav Nagpal v. Sumedha Nagpal).", why]
    if age >= 9:
        welfare.append("Child may be old enough to express an intelligent preference (s.17 GWA); the court may ascertain it in camera. Age thresholds are discretionary.")
    if special:
        welfare.append("Special medical/education needs: keep routines, therapies and school unchanged; share provider contacts.")
    phase = ["Phase 1 (first 8 weeks): day contact only (Sat 10:00-17:00, Wed 17:00-19:00); overnights added in Phase 2."] if age < 3 else []
    plan = {"primary_parent": primary, "chosen": chosen, "alternatives": sorted(cands, key=lambda c: -c["score"]),
            "calendar": calendar(chosen["pattern"], primary, dt.date.today(), 8), "holidays": holidays(age, dist, primary),
            "dispute_clauses": clauses(), "welfare_notes": welfare, "phase_in": phase, "note": note}
    plan["agreement_text"] = agreement_text(inp, plan)
    plan["ai_mode"] = llm.mode()
    return plan


def agreement_text(inp, plan):
    a, b = inp["parent_a"].get("name", "Parent A"), inp["parent_b"].get("name", "Parent B")
    child = inp["child"].get("name", "the child")
    prim = a if plan["primary_parent"] == "A" else b
    base = (f"# PARENTING PLAN AND CUSTODY AGREEMENT\n\nBetween **{a}** (Parent A) and **{b}** (Parent B) concerning **{child}**.\n\n"
            f"## 1. Guiding principle\nThe welfare of the child is paramount.\n\n## 2. Residence\nThe child primarily resides with **{prim}**.\n\n"
            f"## 3. Schedule\n{plan['chosen']['label']}.\n" + "\n".join(plan["phase_in"]) +
            "\n\n## 4. Holidays and vacations\n" + "\n".join(f"- {h}" for h in plan["holidays"]) +
            "\n\n## 5. Dispute resolution and decisions\n" + "\n".join(f"- {c}" for c in plan["dispute_clauses"]) +
            "\n\n## 6. Review\nThe plan is reviewed every 12 months or on a material change in the child's needs.")
    prompt = ("Polish this parenting plan into a clear, neutral, child-centric legal agreement. Do not change any "
              "schedule, number or party name; do not add facts.\n\n" + base)
    return llm.text(prompt, system="You are a family-law drafter.", fallback=base) or base
