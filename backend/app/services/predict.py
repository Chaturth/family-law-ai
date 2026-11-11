"""Module 4 - Predictive settlement & financial analytics.

Honest design note: there is no public labelled dataset of Indian family-court outcomes.
So the engine is (1) a transparent, configurable heuristic, (2) an optional kNN layer that
activates automatically when data/outcomes.csv has >= 30 real rows, and (3) a Monte-Carlo
scenario comparison. All numbers are ILLUSTRATIVE until calibrated on real data by counsel.
"""
import csv
import math
import random
from pathlib import Path
from .. import llm
from ..config import settings

OUTCOMES_CSV = settings.DATA_DIR / "outcomes.csv"
# Columns: payer_income, payee_income, marriage_years, children, maintenance_monthly, payee_asset_share_pct, duration_months

PARAMS = {"band_low": 0.20, "band_high": 0.30, "cap": 0.40, "child_adj": 0.03, "lumpsum_years_factor": 0.4,
          "legal_cost_per_month": 8000, "discount_rate": 0.09}


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def disclosure_check(d):
    """Structure + validate a financial disclosure in the Rajnesh v. Neha spirit."""
    need = {"income": ["monthly_income_payer", "monthly_income_payee"], "assets": ["assets"], "liabilities": ["liabilities"],
            "expenses": ["monthly_expenses_payee"], "dependants": ["children_count"]}
    gaps = [sec for sec, keys in need.items() if any(d.get(k) in (None, "", []) for k in keys)]
    assets = d.get("assets") or []
    tot_a = sum(_f(a.get("value")) for a in assets)
    tot_l = sum(_f(l.get("amount")) for l in d.get("liabilities") or [])
    return {"missing_sections": gaps, "total_assets": tot_a, "total_liabilities": tot_l, "net_worth": tot_a - tot_l,
            "matrimonial_assets": sum(_f(a.get("value")) for a in assets if a.get("kind", "matrimonial") == "matrimonial"),
            "separate_assets": sum(_f(a.get("value")) for a in assets if a.get("kind") == "separate"),
            "complete": not gaps}


def heuristic(d):
    pi, qi = _f(d.get("monthly_income_payer")), _f(d.get("monthly_income_payee"))
    yrs, kids = _f(d.get("marriage_years")), int(_f(d.get("children_count")))
    custody = bool(d.get("payee_has_custody", kids > 0))
    dur = 0.7 if yrs < 3 else 1.0 if yrs <= 10 else 1.15
    adj = PARAMS["child_adj"] * min(kids, 3) if custody else 0
    lo = (PARAMS["band_low"] + adj) * pi * dur - 0.5 * qi
    hi = (PARAMS["band_high"] + adj) * pi * dur - 0.3 * qi
    cap = PARAMS["cap"] * pi
    lo, hi = max(0.0, min(lo, cap)), max(0.0, min(hi, cap))
    if hi < lo:
        lo, hi = hi, lo
    mid = (lo + hi) / 2
    # asset share for payee (% of matrimonial pool)
    base = (15, 30) if yrs < 3 else (25, 45) if yrs <= 10 else (35, 50)
    bonus = (5 if d.get("payee_homemaker") else 0) + (3 if custody else 0)
    share = (min(base[0] + bonus, 50), min(base[1] + bonus, 50))
    # timelines (months)
    tl = {"mutual_consent": (8, 14), "negotiated_mediation": (3, 6), "contested": (24, 48)}
    return {"maintenance_monthly": {"low": round(lo), "mid": round(mid), "high": round(hi)},
            "lump_sum_equivalent": round(mid * 12 * max(1.0, yrs * PARAMS["lumpsum_years_factor"])),
            "payee_asset_share_pct": {"low": share[0], "high": share[1]}, "timeline_months": tl}


def _knn(d, k=7):
    if not OUTCOMES_CSV.exists():
        return None
    rows = list(csv.DictReader(OUTCOMES_CSV.open()))
    if len(rows) < 30:
        return None
    feats = ["payer_income", "payee_income", "marriage_years", "children"]
    q = [_f(d.get("monthly_income_payer")), _f(d.get("monthly_income_payee")), _f(d.get("marriage_years")),
         _f(d.get("children_count"))]
    X = [[_f(r[f]) for f in feats] for r in rows]
    sc = [max(1e-9, (max(c) - min(c))) for c in zip(*X)]
    dist = sorted(((math.sqrt(sum(((a - b) / s) ** 2 for a, b, s in zip(x, q, sc))), r) for x, r in zip(X, rows)),
                  key=lambda t: t[0])[:k]
    ws = [1 / (1e-6 + t[0]) for t in dist]
    avg = lambda key: sum(w * _f(r[key]) for w, (_, r) in zip(ws, dist)) / sum(ws)
    return {"maintenance_monthly": round(avg("maintenance_monthly")), "payee_asset_share_pct": round(avg("payee_asset_share_pct"), 1),
            "duration_months": round(avg("duration_months")), "neighbours": k, "dataset_rows": len(rows)}


def _hist(vals, bins=12):
    lo, hi = min(vals), max(vals)
    w = (hi - lo) / bins or 1
    counts = [0] * bins
    for v in vals:
        counts[min(bins - 1, int((v - lo) / w))] += 1
    s = sorted(vals)
    pct = lambda p: round(s[int(p * (len(s) - 1))])
    return {"edges": [round(lo + i * w) for i in range(bins + 1)], "counts": counts,
            "p10": pct(.1), "p50": pct(.5), "p90": pct(.9), "mean": round(sum(vals) / len(vals))}


def monte_carlo(h, d, n=4000, seed=7):
    """Compare NET value to the payee of litigating vs negotiating (INR, present value)."""
    rnd = random.Random(seed)
    pool = disclosure_check(d)["matrimonial_assets"]
    m = h["maintenance_monthly"]
    sh = h["payee_asset_share_pct"]
    tl = h["timeline_months"]
    r = PARAMS["discount_rate"] / 12
    horizon = int(_f(d.get("maintenance_horizon_months"), 120))
    ann = lambda pm: pm * (1 - (1 + r) ** -horizon) / r

    def trial(contested):
        mm = rnd.triangular(m["low"] * (0.7 if contested else 0.9), m["high"] * (1.2 if contested else 1.05), m["mid"])
        ss = rnd.triangular(sh["low"] * (0.8 if contested else 0.95), sh["high"] * (1.05 if contested else 1.0), (sh["low"] + sh["high"]) / 2)
        months = rnd.triangular(*(tl["contested"] if contested else tl["negotiated_mediation"]),
                                sum(tl["contested"] if contested else tl["negotiated_mediation"]) / 2)
        cost = months * PARAMS["legal_cost_per_month"] * (1.0 if contested else 0.5)
        delay = (1 + r) ** -months
        return (ann(mm) + pool * ss / 100) * delay - cost

    lit = [trial(True) for _ in range(n)]
    neg = [trial(False) for _ in range(n)]
    prob = sum(1 for a, b in zip(lit, neg) if b >= a) / n
    return {"litigated": _hist(lit), "negotiated": _hist(neg), "prob_negotiated_beats_litigated": round(prob, 3)}


def predict(d):
    chk = disclosure_check(d)
    h = heuristic(d)
    knn = _knn(d)
    out = {"disclosure": chk, "heuristic": h, "knn": knn,
           "data_source": "kNN blended with heuristic" if knn else "heuristic only (no outcomes.csv with >=30 rows)",
           "scenarios": monte_carlo(h, d), "params": PARAMS}
    if knn:
        h["maintenance_monthly"]["mid"] = round((h["maintenance_monthly"]["mid"] + knn["maintenance_monthly"]) / 2)
    prompt = ("Explain these illustrative family-court projections to a litigant in plain English (<=180 words). State "
              "assumptions, that these are estimates not predictions of any judge, and that Rajnesh v. Neha-style "
              f"disclosure drives actual orders.\nINPUT: {d}\nRESULT: {out['heuristic']} {out['scenarios']}")
    out["narrative"] = llm.text(prompt, system="You are a cautious legal analytics explainer.",
                                fallback="Estimates come from a transparent heuristic (income-share bands adjusted for marriage "
                                         "duration, children and the payee's income) and a Monte-Carlo comparison of litigating "
                                         "versus negotiating. They are illustrative, not a prediction of any court.")
    out["ai_mode"] = llm.mode()
    return out


def tradeoff(d):
    """Equivalence between a one-off asset concession and a monthly maintenance reduction (NPV)."""
    r = _f(d.get("discount_rate"), PARAMS["discount_rate"]) / 12
    n = int(_f(d.get("horizon_months"), 120))
    af = (1 - (1 + r) ** -n) / r
    out = {"annuity_factor": round(af, 3), "horizon_months": n}
    if d.get("asset_value_given_up") is not None:
        v = _f(d["asset_value_given_up"])
        out["equivalent_monthly_reduction"] = round(v / af)
    if d.get("monthly_reduction") is not None:
        out["equivalent_asset_value"] = round(_f(d["monthly_reduction"]) * af)
    if "current_monthly" in d and "equivalent_monthly_reduction" in out:
        out["new_monthly"] = max(0, round(_f(d["current_monthly"]) - out["equivalent_monthly_reduction"]))
    out["note"] = "Present-value equivalence only; ignore tax, remarriage/termination clauses and indexation - have counsel review."
    return out
