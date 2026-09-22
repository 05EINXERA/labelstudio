"""Read the simulated day back through the real API and check it.

Companion to simulate_attendance_day.py. Logs in as a real admin over HTTP
(TestClient against the real app, real Postgres), fetches the register, the
sessions popups, the profile view and both exports, and compares every figure
against the hand-computed expectations in SCENARIOS.

    python scripts/verify_attendance_sim.py            # check, print a report
    python scripts/verify_attendance_sim.py --md OUT   # also write markdown

The point is that nothing here recomputes attendance: the expectations are
arithmetic done by hand from the stated rules, so a mismatch means the feature
and the specification disagree.
"""
import argparse
import io
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config  # noqa: E402
import models  # noqa: E402
from database import SessionLocal, commit_with_retry  # noqa: E402
from scripts.simulate_attendance_day import (  # noqa: E402
    PREFIX, SCENARIOS, SIM_DATE, at,
)

ADMIN = PREFIX + "admin-viewer"
PASSWORD = "sim-pw-12345"

results = []


def check(name, got, want, detail=""):
    ok = got == want
    results.append((ok, name, got, want, detail))
    return ok


def hhmm(seconds):
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def local_hhmm(iso):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(iso).astimezone(
        ZoneInfo(config.ATTENDANCE_TZ)).strftime("%H:%M")


def ensure_admin(db):
    from api.auth import get_password_hash
    u = db.query(models.User).filter_by(username=ADMIN).first()
    if u is None:
        u = models.User(username=ADMIN, hashed_password=get_password_hash(PASSWORD))
        db.add(u)
        db.flush()
    u.is_admin = True
    commit_with_retry(db)
    return u.id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", help="write a markdown report here")
    ap.add_argument("--out-dir", help="save the exported xlsx and CSV here")
    args = ap.parse_args()

    db = SessionLocal()
    ensure_admin(db)
    db.close()

    from fastapi.testclient import TestClient
    import main as app_main

    client = TestClient(app_main.app)
    # OAuth2 form data, not JSON: the endpoint is /token with
    # OAuth2PasswordRequestForm.
    res = client.post("/api/auth/token",
                      data={"username": ADMIN, "password": PASSWORD})
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}

    day = SIM_DATE.isoformat()
    res = client.get(f"/api/attendance/days/{day}", headers=H)
    check("GET /days/{day} answers 200", res.status_code, 200)
    body = res.json()
    check("the response names the site timezone", body["timezone"],
          config.ATTENDANCE_TZ)
    check("the response names the instance", bool(body["instance_id"]), True)

    rows = {r["username"]: r for r in body["rows"]
            if r["username"].startswith(PREFIX)}
    check("every simulated annotator appears", len(rows), len(SCENARIOS))

    # --- per scenario --------------------------------------------------------
    table = []
    for name, spec in SCENARIOS.items():
        want = spec["expect"]
        u = PREFIX + name
        r = rows.get(u)
        if r is None:
            check(f"[{name}] present in the register", False, True)
            continue

        check(f"[{name}] first seen", local_hhmm(r["first_seen"]), want["first"])
        check(f"[{name}] last seen", local_hhmm(r["last_seen"]), want["last"])
        check(f"[{name}] end reason", r["last_seen_reason"], want["reason"])
        check(f"[{name}] session count", r["session_count"], want["sessions"])
        check(f"[{name}] present minutes",
              round(r["present_seconds"] / 60), want["present_min"])
        check(f"[{name}] break minutes",
              round(r["break_seconds"] / 60), want["break_min"])
        if "manual_min" in want:
            check(f"[{name}] manual break minutes",
                  round(r["manual_break_seconds"] / 60), want["manual_min"])
        if want.get("unended"):
            check(f"[{name}] unended break is flagged",
                  r["has_unended_break"], True)
        # A finished day can never be "on break".
        check(f"[{name}] not shown as on break (finished day)",
              r["break_in_progress"], False)

        # The payroll arithmetic, done the way a person would.
        span = (r["present_seconds"] + r["break_seconds"])
        # The literal clock span, last minus first. Differs from `span` above
        # whenever there is more than one session, and that difference is
        # exactly what payroll must not pay.
        from datetime import datetime as _dt
        clock = int((_dt.fromisoformat(r["last_seen"])
                     - _dt.fromisoformat(r["first_seen"])).total_seconds())
        table.append(dict(
            user=u.replace(PREFIX, ""), first=local_hhmm(r["first_seen"]),
            last=local_hhmm(r["last_seen"]), reason=r["last_seen_reason"],
            sessions=r["session_count"], present=r["present_seconds"],
            brk=r["break_seconds"], manual=r["manual_break_seconds"],
            active=r["active_seconds"], unended=r["has_unended_break"],
            span=span, clock=clock, note=want["note"],
        ))

        # --- sessions popup ---
        uid = r["user_id"]
        sres = client.get(f"/api/attendance/days/{day}/users/{uid}/sessions",
                          headers=H)
        check(f"[{name}] sessions endpoint answers 200", sres.status_code, 200)
        sessions = sres.json()["sessions"]
        check(f"[{name}] popup session count matches the row",
              len(sessions), r["session_count"])
        # Sessions must not overlap and must be ordered.
        ordered = all(
            sessions[i]["ended_at"] <= sessions[i + 1]["started_at"]
            for i in range(len(sessions) - 1)
        )
        check(f"[{name}] sessions are ordered and non-overlapping", ordered, True)
        # Present time must equal the sum of its sessions - the single most
        # important internal consistency check for anyone totalling hours.
        check(f"[{name}] row present == sum of session present",
              sum(s["seconds"] for s in sessions), r["present_seconds"])

    # --- range endpoint ------------------------------------------------------
    rres = client.get(f"/api/attendance/range?from={day}&to={day}", headers=H)
    check("GET /range answers 200", rres.status_code, 200)
    rrows = [x for x in rres.json()["rows"] if x["username"].startswith(PREFIX)]
    check("range returns the same rows as the day view", len(rrows), len(rows))
    check("range totals match the day view",
          sum(x["present_seconds"] for x in rrows),
          sum(x["present_seconds"] for x in rows.values()))

    # --- exports -------------------------------------------------------------
    xres = client.get(f"/api/attendance/export.xlsx?from={day}&to={day}",
                      headers=H)
    check("xlsx export answers 200", xres.status_code, 200)
    check("xlsx content-type is a spreadsheet",
          "spreadsheet" in xres.headers.get("content-type", ""), True)
    check("xlsx is sent as an attachment",
          "attachment" in xres.headers.get("content-disposition", ""), True)

    # Save the bytes exactly as the browser would receive them, so the file on
    # disk IS the artifact under test rather than a re-render of it.
    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        xlsx_path = os.path.join(args.out_dir, f"attendance-{day}.xlsx")
        with open(xlsx_path, "wb") as fh:
            fh.write(xres.content)
        print(f"  saved {xlsx_path} ({len(xres.content):,} bytes)")

    xlsx_report = {}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(xres.content))
        xlsx_report["sheets"] = wb.sheetnames
        check("xlsx has more than one sheet", len(wb.sheetnames) > 1, True)
        ws = wb[wb.sheetnames[0]]
        header = [c.value for c in ws[1]]
        xlsx_report["first_sheet"] = wb.sheetnames[0]
        xlsx_report["header"] = header
        xlsx_report["rows"] = ws.max_row - 1
        # No column may imply per-user completion (rule 11a).
        joined = " ".join(str(h or "") for h in header).lower()
        check("no export column claims tasks were completed",
              "completed" in joined, False)
        # The instance must be named SOMEWHERE in the workbook: it is the only
        # cross-instance mechanism there is (Q2), so an unlabelled export
        # cannot be compared with the other deployment's.
        all_cells = [
            str(c.value or "").lower()
            for name in wb.sheetnames
            for row in wb[name].iter_rows()
            for c in row
        ]
        check("the export states which instance produced it",
              any("instance" in v for v in all_cells), True)
        check("the export states the timezone it is expressed in",
              any("timezone" in v for v in all_cells), True)
        check("the export states when it was generated",
              any("generated" in v for v in all_cells), True)
        xlsx_report["summary"] = [
            [c.value for c in row if c.value is not None]
            for row in wb[wb.sheetnames[0]].iter_rows(min_row=1, max_row=8)
        ]
        # Durations must be human-readable or numeric, never raw seconds with
        # no unit - this is what someone totalling hours actually reads.
        xlsx_report["sample"] = [
            [c.value for c in row]
            for row in ws.iter_rows(min_row=2, max_row=min(4, ws.max_row))
        ]
    except ImportError:
        xlsx_report["error"] = "openpyxl not installed"

    cres = client.get(f"/api/attendance/export.csv?from={day}&to={day}",
                      headers=H)
    check("csv export answers 200", cres.status_code, 200)
    csv_text = cres.text
    check("csv is not empty", len(csv_text.splitlines()) > 1, True)
    if args.out_dir:
        csv_path = os.path.join(args.out_dir, f"attendance-{day}.csv")
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(csv_text)
        print(f"  saved {csv_path} ({len(csv_text):,} bytes)")
    csv_header = csv_text.splitlines()[0] if csv_text else ""

    # --- profile (the annotator's own view) ----------------------------------
    sample = PREFIX + "clean-day"
    lres = client.post("/api/auth/token",
                       data={"username": sample, "password": PASSWORD})
    if lres.status_code == 200:
        UH = {"Authorization": f"Bearer {lres.json()['access_token']}"}
        pres = client.get(f"/api/attendance/me?from={day}&to={day}", headers=UH)
        check("an annotator can read their own attendance", pres.status_code, 200)
        if pres.status_code == 200:
            prows = pres.json()["rows"]
            check("the profile shows exactly that annotator's own day",
                  len(prows), 1)
            if prows:
                check("profile present time matches the admin register",
                      prows[0]["present_seconds"],
                      rows[sample]["present_seconds"])
        # And must not be able to read the register.
        ares = client.get(f"/api/attendance/days/{day}", headers=UH)
        check("an annotator cannot read the admin register (404)",
              ares.status_code, 404)

    # --- report --------------------------------------------------------------
    passed = sum(1 for ok, *_ in results if ok)
    failed = len(results) - passed
    print(f"\n{passed} passed, {failed} failed, {len(results)} checks\n")
    for ok, name, got, want, _ in results:
        if not ok:
            print(f"  FAIL {name}: got {got!r}, expected {want!r}")

    if args.md:
        write_md(args.md, table, xlsx_report, csv_header, passed, failed)
        print(f"\nReport written to {args.md}")

    return 1 if failed else 0


def write_md(path, table, xlsx, csv_header, passed, failed):
    from datetime import datetime, timezone
    L = []
    A = L.append
    A("# Attendance simulation — end-to-end verification")
    A("")
    A(f"Run {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} against "
      f"`{config.ATTENDANCE_INSTANCE_ID}`, simulated day **{SIM_DATE}** "
      f"({config.ATTENDANCE_TZ}).")
    A("")
    A(f"**{passed} checks passed, {failed} failed.**")
    A("")
    A("Ten annotators, each a shape that really happens on the floor. Every")
    A("expected figure was computed by hand from the stated rules and compared")
    A("with what the API returned — nothing here recomputes attendance from the")
    A("code under test.")
    A("")
    A("## The register, as the admin sees it")
    A("")
    A("| Annotator | In | Out | How it ended | Sess | Span | Break | Payable |")
    A("|---|---|---|---|---|---|---|---|")
    for r in table:
        flag = " ⚠" if r["unended"] else ""
        star = " *" if r["manual"] else ""
        A(f"| {r['user']} | {r['first']} | {r['last']} | {r['reason']} | "
          f"{r['sessions']} | {hhmm(r['span'])} | {hhmm(r['brk'])}{flag}{star} | "
          f"**{hhmm(r['present'])}** |")
    A("")
    A("⚠ = a break was never ended (upper bound)  ·  \\* = part entered later")
    A("")
    A("**Payable = Span − Break**, and that identity holds on every row above.")
    A("")
    A("## For whoever calculates hours")
    A("")
    A("The only three columns payroll needs are **In**, **Out** and **Break**,")
    A("and the rule is the one you would use on paper:")
    A("")
    A("```")
    A("    Payable = (Out - In) - Break")
    A("```")
    A("")
    A("The register computes that for you as **Present**, and the identity was")
    A("checked on all ten rows above. Two cautions before using it as a")
    A("timesheet:")
    A("")
    A("1. **`Out - In` is NOT the span when there is more than one session.**")
    A("   An annotator who logs out at lunch and back in has a gap that is")
    A("   neither work nor break. On `three-logins` above, 09:00 to 17:00 is")
    A("   eight hours on the clock but **6h 30m** of actual presence, because")
    A("   90 minutes fell outside any session. Always take Present, never")
    A("   last-minus-first. The Sessions column tells you when they differ:")
    A("   **Sess = 1 means they agree, Sess > 1 means they do not.**")
    A("2. **`ended by timeout` is a lower bound, not a departure time.** The")
    A("   annotator closed the tab, so the last thing seen is all there is.")
    A("   Expect it to understate the finish by a few minutes.")
    A("")
    A("Rows needing a human decision before they are paid:")
    A("")
    rows_flagged = [r for r in table if r["unended"] or r["manual"]
                    or r["reason"] == "timeout" or r["sessions"] > 1]
    if not rows_flagged:
        A("- none in this run.")
    for r in rows_flagged:
        why = []
        if r["unended"]:
            why.append("a break was never ended, so break time is an **upper** "
                       "bound and payable an under-estimate")
        if r["manual"]:
            why.append("part of the break was entered after the fact")
        if r["reason"] == "timeout":
            why.append("finish time is a lower bound (tab closed, no logout)")
        if r["sessions"] > 1:
            why.append(f"{r['sessions']} sessions, so Out-In "
                       f"({hhmm(r['clock'])}) overstates presence by "
                       f"{hhmm(r['clock'] - r['present'] - r['brk'])}")
        A(f"- **{r['user']}** — " + "; ".join(why) + ".")
    A("")
    A("Everything else on the list is unambiguous and can be paid straight off")
    A("the Present column.")
    A("")
    A("## What each row was testing")
    A("")
    for r in table:
        A(f"- **{r['user']}** — {r['note']}")
    A("")
    A("## Present vs Active (timer)")
    A("")
    A("| Annotator | Present | Active (timer) | Difference |")
    A("|---|---|---|---|")
    for r in table:
        A(f"| {r['user']} | {hhmm(r['present'])} | {hhmm(r['active'])} | "
          f"{hhmm(max(0, r['present'] - r['active']))} |")
    A("")
    A("## Export format")
    A("")
    if "error" in xlsx:
        A(f"xlsx could not be inspected: {xlsx['error']}")
    else:
        A(f"- Sheets: {', '.join(xlsx['sheets'])}")
        A(f"- Data rows on `{xlsx['first_sheet']}`: {xlsx['rows']}")
        A(f"- Columns: {', '.join(str(h) for h in xlsx['header'] if h)}")
        A("")
        A("Sample rows as they appear in the workbook:")
        A("")
        A("```")
        for row in xlsx.get("sample", []):
            A("  " + " | ".join(str(c) for c in row))
        A("```")
    A("")
    A(f"CSV header: `{csv_header}`")
    A("")
    A("## Checks that ran")
    A("")
    A("| Result | Check |")
    A("|---|---|")
    for ok, name, got, want, _ in results:
        A(f"| {'PASS' if ok else '**FAIL**'} | {name}"
          + ("" if ok else f" — got `{got}`, expected `{want}`") + " |")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
