"""Curated starter knowledge base of Indian family-law authorities.

IMPORTANT: Manupatra, SCC Online and eCourts do not offer open public APIs. This KB is a
hand-curated starter set (verified=False: have a lawyer check every entry) with a simple
keyword retriever. Replace `search()` with a licensed-API or vector-store integration later.
"""
import re

KB = [
    {"id": "hma-13b", "title": "Hindu Marriage Act, 1955 - s.13B (divorce by mutual consent)",
     "tags": ["divorce", "mutual", "hindu", "13b", "cooling"],
     "text": "Joint petition by both spouses; living separately for one year or more; unable to live together; "
             "mutually agreed to dissolve. Second motion normally after 6 months and within 18 months of the first."},
    {"id": "amardeep", "title": "Amardeep Singh v. Harveen Kaur (2017) 8 SCC 746",
     "tags": ["divorce", "mutual", "cooling", "waiver", "13b"],
     "text": "The 6-month period in s.13B(2) is directory, not mandatory; the court may waive it where the "
             "marriage has broken down irretrievably and settlement terms are in place."},
    {"id": "hma-14", "title": "Hindu Marriage Act, 1955 - s.14",
     "tags": ["divorce", "hindu", "one year"],
     "text": "No divorce petition within one year of marriage unless exceptional hardship or depravity is shown."},
    {"id": "hma-24-25", "title": "Hindu Marriage Act, 1955 - ss.24, 25",
     "tags": ["maintenance", "alimony", "interim", "hindu"],
     "text": "s.24: interim maintenance and litigation expenses. s.25: permanent alimony and maintenance."},
    {"id": "bnss-144", "title": "BNSS, 2023 - s.144 (formerly CrPC s.125)",
     "tags": ["maintenance", "wife", "children", "parents", "125"],
     "text": "Magistrate may order monthly maintenance for wife, minor children and parents who cannot "
             "maintain themselves, where a person with sufficient means neglects or refuses to do so."},
    {"id": "rajnesh", "title": "Rajnesh v. Neha (2021) 2 SCC 324",
     "tags": ["maintenance", "affidavit", "disclosure", "interim", "financial"],
     "text": "Mandatory Affidavit of Assets and Liabilities in all maintenance proceedings; factors for "
             "quantum (standard of living, income, liabilities, needs of children); maintenance payable "
             "from date of application; overlapping jurisdiction guidance."},
    {"id": "gwa-17", "title": "Guardians and Wards Act, 1890 - s.17",
     "tags": ["custody", "guardian", "welfare", "child"],
     "text": "In appointing or declaring a guardian the court shall be guided by the welfare of the minor, "
             "considering age, sex, religion, character, capacity of the proposed guardian and the minor's preference "
             "if the minor is old enough to form an intelligent preference."},
    {"id": "hmga-6", "title": "Hindu Minority and Guardianship Act, 1956 - s.6, s.13",
     "tags": ["custody", "guardian", "hindu", "welfare", "child"],
     "text": "s.6(a): custody of a minor who has not completed five years shall ordinarily be with the mother. "
             "s.13: welfare of the minor is the paramount consideration."},
    {"id": "gaurav-nagpal", "title": "Gaurav Nagpal v. Sumedha Nagpal (2009) 1 SCC 42",
     "tags": ["custody", "welfare", "child"],
     "text": "Welfare of the child is the paramount consideration; statutory personal-law rights of parents "
             "are subordinate to it."},
    {"id": "mediation-act", "title": "Mediation Act, 2023",
     "tags": ["mediation", "settlement", "pre-litigation", "msa"],
     "text": "Framework for pre-litigation and court-annexed mediation; mediated settlement agreements are "
             "enforceable like court decrees once the statutory conditions are met. Certain disputes are "
             "excluded or need court leave - counsel must confirm applicability to the specific dispute."},
    {"id": "fca-9", "title": "Family Courts Act, 1984 - s.9",
     "tags": ["mediation", "settlement", "family court"],
     "text": "Family Court shall make efforts to assist and persuade parties to arrive at a settlement."},
    {"id": "fca-11", "title": "Family Courts Act, 1984 - s.11",
     "tags": ["privacy", "in camera", "confidential", "dpdp"],
     "text": "Proceedings may be held in camera, and shall be in camera if either party so desires."},
    {"id": "fca-13", "title": "Family Courts Act, 1984 - s.13",
     "tags": ["litigant in person", "lawyer", "representation"],
     "text": "No party is entitled as of right to be represented by a legal practitioner; the court may "
             "permit it with leave."},
    {"id": "pwdva", "title": "Protection of Women from Domestic Violence Act, 2005 - s.12, 18-23",
     "tags": ["violence", "emergency", "protection order", "residence", "domestic violence"],
     "text": "Application to Magistrate for protection, residence, monetary relief and custody orders; "
             "interim ex-parte orders available."},
    {"id": "sma-28", "title": "Special Marriage Act, 1954 - s.28",
     "tags": ["divorce", "mutual", "special marriage"],
     "text": "Divorce by mutual consent for marriages under the Special Marriage Act, on lines similar to s.13B HMA."},
    {"id": "divorce-act-10a", "title": "Divorce Act, 1869 - s.10A",
     "tags": ["divorce", "mutual", "christian"],
     "text": "Mutual-consent divorce for Christian spouses after separation of two years or more."},
    {"id": "dpdp", "title": "Digital Personal Data Protection Act, 2023",
     "tags": ["privacy", "dpdp", "consent", "data"],
     "text": "Consent-based processing of digital personal data, purpose limitation, security safeguards, "
             "breach notification and data-principal rights."},
]
for e in KB:
    e["verified"] = False


def _tok(s):
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def search(query, k=5):
    q = _tok(query)
    scored = []
    for e in KB:
        hay = _tok(e["title"] + " " + e["text"]) | set(t for tag in e["tags"] for t in _tok(tag))
        s = len(q & hay) + 2 * len(q & set(t for tag in e["tags"] for t in _tok(tag)))
        if s:
            scored.append((s, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:k]]
