import datetime as dt
import os
import sys
import tempfile
import unittest

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("GEMINI_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import db, exporter  # noqa: E402
from app.services import custody, dispute, intake, mediation, predict  # noqa: E402

FACTS = {"petitioner": {"name": "Anita", "monthly_income": 55000}, "respondent": {"name": "Rahul", "monthly_income": 180000},
         "marriage": {"date": "2016-02-14", "place": "Bengaluru", "personal_law": "hindu"}, "separation_date": "2024-03-01",
         "children": [{"name": "Aarav", "age": 6}], "assets": [{"name": "Flat", "value": 9500000}],
         "liabilities": [{"name": "Loan", "amount": 100000}], "jurisdiction": {"city": "Bengaluru", "court": "Family Court"}}


class T(unittest.TestCase):
    def test_db_encrypted_roundtrip(self):
        db.init()
        i = db.put("case", {"a": 1})
        self.assertEqual(db.get(i)["a"], 1)
        db.update(i, {"b": 2})
        self.assertEqual(db.get(i)["b"], 2)
        raw = open(db.DB_PATH, "rb").read()
        self.assertNotIn(b'"a"', raw)

    def test_draft_and_flags(self):
        d = intake.generate_draft(FACTS, "divorce_13b")
        self.assertIn("Anita", d["text"])
        self.assertEqual(d["missing"], [])
        bad = dict(FACTS, marriage={"date": "2026-06-01", "personal_law": "muslim"}, separation_date="")
        flags = intake.risk_flags(bad, "divorce_13b")
        self.assertTrue(any("Muslim" in f["text"] for f in flags))
        self.assertTrue(any("s.14" in f["text"] for f in flags))
        for t in intake.DOC_TYPES:
            self.assertTrue(intake.generate_draft(FACTS, t)["text"])

    def test_docx(self):
        self.assertTrue(exporter.to_docx("T", "# H\nbody **bold**")[:2] == b"PK")

    def test_mediation_safety(self):
        a = mediation.analyze("A", "You idiot, I will kill you if you take the kids", "")
        self.assertEqual(a["escalation"], 3)
        self.assertIn("threat", a["flags"])
        self.assertEqual(mediation.analyze("B", "I would like weekends with Aarav.")["escalation"], 0)

    def test_custody(self):
        inp = {"child": {"name": "K", "age": 6}, "parent_a": {"name": "A", "role": "mother"}, "parent_b": {"name": "B", "role": "father",
               "available_days": [4, 5, 6]}, "distance_km": 10}
        p = custody.build_plan(inp)
        self.assertEqual(len(p["calendar"]), 56)
        self.assertIn(p["chosen"]["pattern"], custody.PATTERNS)
        far = custody.build_plan(dict(inp, distance_km=200))
        self.assertEqual(far["chosen"]["pattern"], "alt_weekends")  # weekday handovers infeasible when far
        tod = custody.build_plan(dict(inp, child={"name": "K", "age": 2}, proposed_primary=""))
        self.assertEqual(tod["primary_parent"], "A")
        self.assertTrue(tod["phase_in"])
        # calendar must start on a Monday and alternate correctly for week_on_off
        cal = custody.calendar("week_on_off", "A", dt.date(2026, 11, 1), 4)
        self.assertEqual(cal[0]["weekday"], "Mon")
        self.assertEqual({c["with"] for c in cal[:7]}, {"A"})
        self.assertEqual({c["with"] for c in cal[7:14]}, {"B"})

    def test_predict(self):
        d = {"monthly_income_payer": 180000, "monthly_income_payee": 55000, "monthly_expenses_payee": 60000, "marriage_years": 8,
             "children_count": 1, "assets": [{"name": "Flat", "value": 9500000, "kind": "matrimonial"}], "liabilities": []}
        r = predict.predict(d)
        m = r["heuristic"]["maintenance_monthly"]
        self.assertTrue(0 <= m["low"] <= m["mid"] <= m["high"] <= 0.4 * 180000)
        self.assertGreater(r["scenarios"]["litigated"]["p90"], r["scenarios"]["litigated"]["p10"])
        t = predict.tradeoff({"asset_value_given_up": 2000000, "current_monthly": 45000})
        self.assertGreater(t["equivalent_monthly_reduction"], 0)
        back = predict.tradeoff({"monthly_reduction": t["equivalent_monthly_reduction"]})
        self.assertAlmostEqual(back["equivalent_asset_value"], 2000000, delta=500)

    def test_blind_bidding(self):
        issues = [{"key": "m", "label": "Maint"}, {"key": "p", "label": "Payout"}]
        bids = {"A": {"bids": {"m": {"ideal": 25000, "reservation": 40000}, "p": {"ideal": 1e6, "reservation": 2e6}}, "weights": {"m": 8, "p": 4}},
                "B": {"bids": {"m": {"ideal": 60000, "reservation": 35000}, "p": {"ideal": 5e6, "reservation": 3e6}}, "weights": {"m": 5, "p": 9}}}
        r = dispute.resolve(issues, bids)
        self.assertEqual(r["m"]["status"], "agreed")
        self.assertTrue(35000 <= r["m"]["value"] <= 40000)
        self.assertEqual(r["p"]["status"], "gap")
        self.assertNotIn("3000000", str(r["p"]))  # never leak the other side's reservation
        self.assertIn("- Maint:", dispute.agreed_terms({"issues": issues, "results": r}))
        self.assertEqual("", dispute.agreed_terms({"issues": issues, "results": None}))
        neg = {"issues": issues, "round": 1, "bids": {"A": bids["A"]}, "results": None}
        self.assertNotIn("B", str(dispute.party_view(neg, "A")["your_bids"]))

    def test_triage_safety_floor(self):
        r = dispute.triage("He beat me and threatened to take my child")
        self.assertEqual(r["route"], "emergency_court")
        self.assertIn("112", r["helplines"].values())
        self.assertEqual(dispute.triage("We want an amicable settlement")["route"], "pre_litigation_mediation")

    def test_evidence(self):
        a = dispute.tag_evidence({"kind": "bank", "date": "2024-05-01", "text": "Rs 40000 bank transfer"})
        self.assertIn("financial", a["tags"])
        rows = dispute.bundle([{"id": "2", "date": "2024-06-01", "kind": "m", "text": "x", **a}, {"id": "1", "date": "2024-01-01", "kind": "m", "text": "y", **a}])
        self.assertEqual(rows[0]["evidence_id"], "1")
        self.assertEqual(rows[0]["exhibit"], "Ex. P-1")
        self.assertTrue(dispute.hearing_brief(FACTS, rows)["cross_examination_pointers"])


class Usage(unittest.TestCase):
    def test_cap(self):
        from app import llm
        from app.config import settings
        settings.GEMINI_API_KEY, settings.DAILY_LIMIT = "fake", 2
        llm._USAGE.unlink(missing_ok=True)
        calls = []
        llm._call = lambda *a: calls.append(1) or "ok"
        for _ in range(4):
            llm.text("x", fallback="fb")
        self.assertEqual(len(calls), 2)
        self.assertEqual(llm.text("x", fallback="fb"), "fb")
        self.assertEqual(llm.usage()["remaining"], 0)
        settings.GEMINI_API_KEY = ""


if __name__ == "__main__":
    unittest.main(verbosity=2)
