"""Module 1 - Intake, research, drafting, review."""
import datetime as dt
import re
from .. import llm, knowledge

DOC_TYPES = {
    "divorce_13b": "Joint Petition for Divorce by Mutual Consent",
    "custody_application": "Application for Custody / Guardianship of Minor Child",
    "maintenance_bnss144": "Petition for Maintenance (s.144 BNSS / s.125 CrPC)",
    "settlement_agreement": "Domestic Settlement Agreement",
    "financial_affidavit": "Affidavit of Assets, Income and Liabilities (Rajnesh v. Neha format)",
}
MUTUAL = {
    "hindu": ("Hindu Marriage Act, 1955", "section 13B"),
    "special_marriage": ("Special Marriage Act, 1954", "section 28"),
    "christian": ("Indian Divorce Act, 1869", "section 10A"),
}
REQUIRED = {
    "divorce_13b": ["petitioner.name", "respondent.name", "marriage.date", "marriage.place",
                    "marriage.personal_law", "separation_date", "jurisdiction.city", "jurisdiction.court"],
    "custody_application": ["petitioner.name", "respondent.name", "children", "jurisdiction.court"],
    "maintenance_bnss144": ["petitioner.name", "respondent.name", "marriage.date",
                            "respondent.monthly_income", "petitioner.monthly_income", "jurisdiction.court"],
    "settlement_agreement": ["petitioner.name", "respondent.name", "marriage.date"],
    "financial_affidavit": ["petitioner.name", "petitioner.monthly_income", "assets", "liabilities"],
}


def get_path(d, path):
    for p in path.split("."):
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d if d not in ("", [], {}, None) else None


def merge_facts(base, extra, override=False):
    """Deep merge. override=False only fills gaps (used for AI extraction)."""
    for k, v in (extra or {}).items():
        if v in ("", None, [], {}):
            continue
        if isinstance(v, dict):
            base[k] = merge_facts(base.get(k) if isinstance(base.get(k), dict) else {}, v, override)
        elif isinstance(v, list):
            cur = base.get(k) if isinstance(base.get(k), list) else []
            base[k] = cur + [x for x in v if x not in cur]
        elif override or base.get(k) in ("", None):
            base[k] = v
    return base


def _years_between(a, b=None):
    try:
        d1 = dt.date.fromisoformat(a)
        d2 = dt.date.fromisoformat(b) if b else dt.date.today()
        return round((d2 - d1).days / 365.25, 1)
    except Exception:
        return None


def derived(facts):
    return {"marriage_years": _years_between(get_path(facts, "marriage.date") or ""),
            "separation_years": _years_between(facts.get("separation_date") or ""),
            "children_count": len(facts.get("children") or [])}


# ---------------------------------------------------------------- extraction
EXTRACT_SCHEMA = """Return JSON with keys (omit unknown, never guess):
petitioner{name,age,occupation,address,monthly_income}, respondent{same}, marriage{date(YYYY-MM-DD),place,
personal_law(hindu|special_marriage|christian|muslim|other)}, separation_date(YYYY-MM-DD),
children[{name,age}], assets[{name,value,owner}], liabilities[{name,amount}],
jurisdiction{city,state,court}, existing_orders[string], dispute_summary(string, <=60 words)."""


def _regex_extract(text):
    out = {}
    m = re.search(r"married (?:on|in)\s+(\d{4}-\d{2}-\d{2})", text, re.I)
    if m:
        out["marriage"] = {"date": m.group(1)}
    m = re.search(r"separat\w+ (?:since|on|from)\s+(\d{4}-\d{2}-\d{2})", text, re.I)
    if m:
        out["separation_date"] = m.group(1)
    return out


def extract_entities(text, files=None):
    prompt = f"Extract case facts from this Indian family-law intake material.\n{EXTRACT_SCHEMA}\n\nMATERIAL:\n{text or '(see attached file)'}"
    return llm.json_(prompt, system="You are a careful legal intake clerk. Extract only what is explicitly stated.",
                     files=files, fallback=lambda: _regex_extract(text or ""))


# ---------------------------------------------------------------- checks
def missing_disclosures(facts, doc_type):
    return [p for p in REQUIRED.get(doc_type, []) if get_path(facts, p) is None]


def risk_flags(facts, doc_type):
    d, flags = derived(facts), []
    law = get_path(facts, "marriage.personal_law")
    if doc_type == "divorce_13b":
        if law == "muslim":
            flags.append({"level": "high", "text": "Muslim personal law: s.13B HMA does not apply. Counsel must choose the correct route (e.g. mubarat/khula/DMMA)."})
        if d["marriage_years"] is not None and d["marriage_years"] < 1:
            flags.append({"level": "high", "text": "Marriage under 1 year: s.14 HMA bars divorce petitions unless exceptional hardship/depravity is pleaded."})
        if d["separation_years"] is None:
            flags.append({"level": "high", "text": "Separation date missing: one-year living-apart requirement cannot be verified."})
        elif d["separation_years"] < 1:
            flags.append({"level": "high", "text": "Separated for less than 1 year: s.13B(1) condition not yet met."})
        flags.append({"level": "medium", "text": "Second motion normally after 6 months (waivable per Amardeep Singh v. Harveen Kaur, at court's discretion)."})
        if d["children_count"] and not facts.get("custody_terms"):
            flags.append({"level": "medium", "text": "Minor children but no agreed custody/visitation terms; courts expect them in the settlement."})
    if doc_type in ("maintenance_bnss144", "financial_affidavit") and not facts.get("assets"):
        flags.append({"level": "high", "text": "Rajnesh v. Neha requires a full Affidavit of Assets and Liabilities; assets are empty."})
    if not get_path(facts, "jurisdiction.court"):
        flags.append({"level": "medium", "text": "Jurisdiction/court not identified (check s.19 HMA / s.7 Family Courts Act territorial rules)."})
    if facts.get("existing_orders"):
        flags.append({"level": "medium", "text": "Existing orders present: check for conflicts or pending proceedings in other courts."})
    return flags


# ---------------------------------------------------------------- templates
def _v(x):
    return str(x) if x not in (None, "", [], {}) else "[●]"


def _parties(f):
    p, r = f.get("petitioner", {}), f.get("respondent", {})
    return p, r


def _children_block(f):
    ch = f.get("children") or []
    if not ch:
        return "No minor children were born out of the wedlock."
    return "\n".join(f"{i}. {_v(c.get('name'))}, aged {_v(c.get('age'))} years" for i, c in enumerate(ch, 1))


def _money(label, rows, key):
    if not rows:
        return f"{label}: [●]"
    return "\n".join(f"- {_v(r.get('name'))}: Rs. {_v(r.get(key))}" + (f" ({r['owner']})" if r.get("owner") else "") for r in rows)


def render_template(doc_type, facts, authorities):
    p, r = _parties(facts)
    m, j = facts.get("marriage", {}), facts.get("jurisdiction", {})
    court = _v(j.get("court"))
    head = f"IN THE {court.upper()}\nAT {_v(j.get('city'))}, {_v(j.get('state'))}\n"
    auth = "\n".join(f"- {a['title']}" for a in authorities) or "- [●]"
    if doc_type == "divorce_13b":
        act, sec = MUTUAL.get(m.get("personal_law"), ("[applicable personal law]", "[section]"))
        return f"""{head}
# JOINT PETITION UNDER {sec.upper()} OF THE {act.upper()}

**{_v(p.get('name'))}** ... Petitioner No.1
**{_v(r.get('name'))}** ... Petitioner No.2

## MOST RESPECTFULLY SHOWETH
1. The parties were married on {_v(m.get('date'))} at {_v(m.get('place'))} according to the applicable rites and customs.
2. The parties last resided together at {_v(p.get('address'))}; the Court has territorial jurisdiction.
3. The parties have been living separately since {_v(facts.get('separation_date'))}, i.e. for one year or more, and have been unable to live together.
4. The parties have mutually agreed that the marriage has broken down irretrievably and should be dissolved.
5. Children of the marriage:
{_children_block(facts)}
6. Terms of settlement (maintenance/alimony, streedhan, custody and visitation, division of property): {_v(facts.get('settlement_terms'))}
7. No other proceeding between the parties is pending except: {_v('; '.join(facts.get('existing_orders') or []))}.
8. The consent of both parties is free, voluntary and not obtained by force, fraud or undue influence.

## PRAYER
(a) pass a decree of divorce by mutual consent dissolving the marriage solemnised on {_v(m.get('date'))};
(b) incorporate the settlement terms in the decree;
(c) waive the statutory waiting period, if the Court deems fit, in view of the settlement;
(d) pass any other order the Court deems fit.

Place: {_v(j.get('city'))}    Date: [●]
Petitioner No.1: ____________    Petitioner No.2: ____________

## VERIFICATION
Verified at {_v(j.get('city'))} that the contents above are true to our knowledge and belief.

### Authorities considered (for counsel)
{auth}"""
    if doc_type == "custody_application":
        return f"""{head}
# APPLICATION FOR CUSTODY OF MINOR CHILD(REN)

**{_v(p.get('name'))}** ... Applicant   v.   **{_v(r.get('name'))}** ... Respondent

## FACTS
1. The parties were married on {_v(m.get('date'))}. Children:
{_children_block(facts)}
2. The child(ren) currently reside with: {_v(facts.get('child_residence'))}.
3. Dispute summary: {_v(facts.get('dispute_summary'))}

## GROUNDS
The welfare of the minor is the paramount consideration (s.17 Guardians and Wards Act, 1890; s.13 Hindu Minority and Guardianship Act, 1956). The Applicant is able to provide a stable home, schooling and care.

## PRAYER
(a) grant custody of the minor child(ren) to the Applicant; (b) grant reasonable visitation to the Respondent; (c) interim custody pending disposal; (d) any other relief.

Date: [●]    Applicant: ____________

### Authorities considered (for counsel)
{auth}"""
    if doc_type == "maintenance_bnss144":
        return f"""{head}
# PETITION FOR MAINTENANCE UNDER SECTION 144 BNSS, 2023 (FORMERLY SECTION 125 CrPC)

**{_v(p.get('name'))}** ... Petitioner   v.   **{_v(r.get('name'))}** ... Respondent

## FACTS
1. Marriage on {_v(m.get('date'))}. Children:
{_children_block(facts)}
2. The Respondent's monthly income is approximately Rs. {_v(r.get('monthly_income'))}; the Petitioner's is Rs. {_v(p.get('monthly_income'))}.
3. The Respondent has neglected/refused to maintain the Petitioner and child(ren). Details: {_v(facts.get('dispute_summary'))}
4. An Affidavit of Assets and Liabilities in the Rajnesh v. Neha format is filed herewith.

## PRAYER
(a) interim maintenance from the date of application; (b) monthly maintenance of Rs. [●]; (c) litigation expenses; (d) any other relief.

Date: [●]    Petitioner: ____________

### Authorities considered (for counsel)
{auth}"""
    if doc_type == "financial_affidavit":
        return f"""# AFFIDAVIT OF ASSETS, INCOME AND LIABILITIES
(In compliance with Rajnesh v. Neha - to be filed by each party)

I, {_v(p.get('name'))}, aged {_v(p.get('age'))}, occupation {_v(p.get('occupation'))}, residing at {_v(p.get('address'))}, solemnly affirm:

## 1. Income
Monthly income: Rs. {_v(p.get('monthly_income'))}. Other income sources: [●]

## 2. Assets
{_money('Assets', facts.get('assets'), 'value')}

## 3. Liabilities
{_money('Liabilities', facts.get('liabilities'), 'amount')}

## 4. Monthly expenses and dependants
Expenses: [●]. Dependants: {_v(len(facts.get('children') or []) or None)}

## 5. Lifestyle / standard of living during marriage
[●]

Deponent: ____________    Verified at {_v(j.get('city'))} on [●]"""
    return f"""# DOMESTIC SETTLEMENT AGREEMENT
(Mediated Settlement Agreement - to be reviewed against the Mediation Act, 2023)

Between **{_v(p.get('name'))}** (First Party) and **{_v(r.get('name'))}** (Second Party), married on {_v(m.get('date'))}.

## 1. Terms
{_v(facts.get('settlement_terms'))}

## 2. Children
{_children_block(facts)}
Custody and visitation: {_v(facts.get('custody_terms'))}

## 3. General
Voluntary consent; full financial disclosure made; confidentiality; governing law of India; disputes first to mediation.

Signed: ____________   ____________    Date: [●]    Witnesses: [●]"""


def generate_draft(facts, doc_type):
    if doc_type not in DOC_TYPES:
        raise ValueError(f"unknown doc_type; choose from {list(DOC_TYPES)}")
    query = f"{doc_type.replace('_', ' ')} {get_path(facts, 'marriage.personal_law') or ''} " \
            f"{'custody child welfare' if facts.get('children') else ''} maintenance affidavit"
    authorities = knowledge.search(query, 5)
    base = render_template(doc_type, facts, authorities)
    missing, risks = missing_disclosures(facts, doc_type), risk_flags(facts, doc_type)
    prompt = (
        "Improve the legal drafting quality of the DRAFT below (clear numbered paragraphs, formal tone, Indian court style).\n"
        "STRICT RULES: do not invent any fact, name, date, amount or case citation; keep every [●] placeholder where data "
        "is missing; only cite authorities from the AUTHORITIES list; keep the headings; output only the final document.\n\n"
        f"FACTS_JSON: {facts}\n\nAUTHORITIES: {[a['title'] for a in authorities]}\n\nDRAFT:\n{base}")
    text = llm.text(prompt, system="You are a senior Indian family-law drafting associate.", fallback=base)
    return {"doc_type": doc_type, "title": DOC_TYPES[doc_type], "text": text or base, "risks": risks,
            "missing": missing, "authorities": authorities, "status": "pending_review",
            "reviewer_notes": "", "ai_mode": llm.mode()}
