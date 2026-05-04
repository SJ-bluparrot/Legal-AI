"""
Stress test — 50 attorney-realistic scenarios covering every edge case.
Run: python3 tests/stress_test_50.py
"""
import requests, json, time, sys

BASE    = "http://localhost:9000"
API_KEY = "Saul_Lm-BluParrot124"
HEADERS = {"Content-Type": "application/json", "X-API-Key": API_KEY}
TIMEOUT      = 300   # GPU inference can take 60-90s for complex queries
DELAY_BETWEEN = 3    # seconds between requests — respects 30/min rate limit

SCENARIOS = [
    # ── GREETINGS / CONVERSATIONAL ─────────────────────────────────────────
    ("GREETING",  "hi"),
    ("GREETING",  "hello"),
    ("GREETING",  "hey there"),
    ("GREETING",  "good morning"),
    ("GREETING",  "what can you do"),
    ("GREETING",  "who are you"),
    ("GREETING",  "can you help me"),
    ("GREETING",  "how does this work"),
    ("GREETING",  "i need help"),
    ("GREETING",  "get me started"),

    # ── VAGUE / INCOMPLETE — no hard facts yet ─────────────────────────────
    ("VAGUE",     "my client has a problem"),
    ("VAGUE",     "we have a situation"),
    ("VAGUE",     "my client was hurt"),
    ("VAGUE",     "someone did something bad to my client"),
    ("VAGUE",     "my client wants to sue"),
    ("VAGUE",     "there was an incident last month"),
    ("VAGUE",     "my client is in trouble"),
    ("VAGUE",     "I need to file something"),

    # ── PERSONAL INJURY ────────────────────────────────────────────────────
    ("PERSONAL_INJURY", "My client was rear-ended on the BQE last Tuesday, fractured two ribs"),
    ("PERSONAL_INJURY", "Client slipped on an icy sidewalk outside a deli in the Bronx, broke her wrist"),
    ("PERSONAL_INJURY", "My client was bitten by a neighbor's dog in Queens, needed 12 stitches"),
    ("PERSONAL_INJURY", "Client fell down broken stairs at a Manhattan apartment building"),
    ("PERSONAL_INJURY", "Scaffolding collapsed on my client near a midtown construction site"),
    ("PERSONAL_INJURY", "My client was hit by a delivery truck while crossing Broadway"),

    # ── EMPLOYMENT DISPUTE ─────────────────────────────────────────────────
    ("EMPLOYMENT", "My client was fired after reporting sexual harassment to HR in Brooklyn"),
    ("EMPLOYMENT", "Client is owed 3 months of unpaid overtime at a restaurant in Queens"),
    ("EMPLOYMENT", "My client was passed over for promotion because of her pregnancy"),
    ("EMPLOYMENT", "Employer deducted tips from my client's paycheck illegally"),
    ("EMPLOYMENT", "Client was terminated the day after filing a workers comp claim"),
    ("EMPLOYMENT", "My client was let go last Friday with no explanation after 8 years"),

    # ── CRIMINAL DEFENSE ───────────────────────────────────────────────────
    ("CRIMINAL",  "My client was arrested for DWI on the LIE last night, .12 BAC"),
    ("CRIMINAL",  "Client charged with assault after a bar fight in Manhattan"),
    ("CRIMINAL",  "My client is accused of petit larceny at a Walgreens in the Bronx"),
    ("CRIMINAL",  "Client was indicted for wire fraud, federal court SDNY"),

    # ── CONTRACT DISPUTE ───────────────────────────────────────────────────
    ("CONTRACT",  "My client paid a contractor $40k upfront, work was never done"),
    ("CONTRACT",  "Vendor delivered defective equipment, refuses to refund"),
    ("CONTRACT",  "Client signed a non-compete that seems overly broad under NY law"),

    # ── PROPERTY / EMINENT DOMAIN ──────────────────────────────────────────
    ("PROPERTY",  "Someone broke into my client's warehouse and stole $30k in equipment"),
    ("PROPERTY",  "Neighbor keeps parking on my client's private driveway in Staten Island"),
    ("PROPERTY",  "City condemned my client's building in Brooklyn without fair compensation"),

    # ── FAMILY LAW ─────────────────────────────────────────────────────────
    ("FAMILY",    "My client wants to file for divorce after 12 years of marriage in Queens"),
    ("FAMILY",    "Client's ex is not paying child support ordered by the court"),
    ("FAMILY",    "My client wants sole custody, ex has substance abuse issues"),

    # ── OFF-TOPIC / NON-LEGAL ──────────────────────────────────────────────
    ("OFF_TOPIC", "how do I fix my car engine"),
    ("OFF_TOPIC", "what is the best restaurant in Manhattan"),
    ("OFF_TOPIC", "write me a Python script"),
    ("OFF_TOPIC", "what's the weather like today"),

    # ── WRONG JURISDICTION ─────────────────────────────────────────────────
    ("WRONG_JURISDICTION", "My client slipped and fell in Los Angeles"),
    ("WRONG_JURISDICTION", "Employment dispute in Texas, client was wrongfully terminated"),
]

PASS   = "\033[92mPASS\033[0m"
FAIL   = "\033[91mFAIL\033[0m"
WARN   = "\033[93mWARN\033[0m"

def run():
    results = []
    print(f"\n{'─'*80}")
    print(f"  Nyaay AI Stress Test — {len(SCENARIOS)} scenarios")
    print(f"{'─'*80}\n")

    for i, (category, query) in enumerate(SCENARIOS, 1):
        t0 = time.time()
        try:
            resp = requests.post(
                f"{BASE}/questions",
                headers=HEADERS,
                json={"question": query},
                timeout=TIMEOUT,
            )
            elapsed = time.time() - t0

            if resp.status_code != 200:
                status = FAIL
                case_type = "HTTP_ERROR"
                answer_preview = f"HTTP {resp.status_code}"
                claude_used = "?"
            else:
                data = resp.json()
                case_type   = data.get("case_type", "?")
                low_conf    = data.get("classification_low_confidence", False)
                offer       = data.get("offer_complaint", False)
                answer      = data.get("answer", "")
                answer_preview = answer[:80].replace("\n", " ")
                claude_used = "YES" if offer or (case_type not in ("other","unsupported") and not low_conf) else "no"
                status = PASS

            results.append({
                "i": i, "category": category, "query": query[:50],
                "case_type": case_type, "elapsed": elapsed,
                "status": status, "answer": answer_preview,
                "claude": claude_used,
            })

            print(f"[{i:02d}] {status} | {category:<20} | {case_type:<18} | {elapsed:5.1f}s | Claude={claude_used}")
            print(f"      Q: {query[:70]}")
            print(f"      A: {answer_preview[:75]}")
            print()

        except Exception as e:
            elapsed = time.time() - t0
            results.append({
                "i": i, "category": category, "query": query[:50],
                "case_type": "EXCEPTION", "elapsed": elapsed,
                "status": FAIL, "answer": str(e), "claude": "?",
            })
            print(f"[{i:02d}] {FAIL} | {category:<20} | EXCEPTION | {elapsed:5.1f}s")
            print(f"      Q: {query[:70]}")
            print(f"      E: {str(e)[:75]}")
            print()

        time.sleep(DELAY_BETWEEN)

    # ── Summary ──────────────────────────────────────────────────────────
    total     = len(results)
    passed    = sum(1 for r in results if "PASS" in r["status"])
    failed    = total - passed
    avg_time  = sum(r["elapsed"] for r in results) / total
    max_time  = max(r["elapsed"] for r in results)
    min_time  = min(r["elapsed"] for r in results)
    claude_calls = sum(1 for r in results if r["claude"] == "YES")

    print(f"\n{'═'*80}")
    print(f"  RESULTS: {passed}/{total} passed | {failed} failed")
    print(f"  Time:    avg={avg_time:.1f}s  min={min_time:.1f}s  max={max_time:.1f}s")
    print(f"  Claude API calls: {claude_calls}/{total} ({claude_calls/total*100:.0f}%)")
    print(f"{'═'*80}\n")

    if failed:
        print("FAILED scenarios:")
        for r in results:
            if "FAIL" in r["status"]:
                print(f"  [{r['i']:02d}] {r['category']} — {r['query']}")
        sys.exit(1)

if __name__ == "__main__":
    run()
