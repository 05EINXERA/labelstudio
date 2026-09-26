"""The network telemetry report (scripts/telemetry_report.py).

Built on a synthetic day whose answers are known, so each view is checked
against arithmetic rather than against itself. The report feeds a network
proposal; a view that silently mis-attributes time (network vs server, list
view vs canvas) would put a wrong number in front of whoever pays for it.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import telemetry_report as tr  # noqa: E402

DAY = "2026-10-01"
T0 = 1_790_000_000_000.0   # epoch ms


def rec(t, cat, **kw):
    return {"t": T0 + t, "cat": cat, "it": kw.pop("it", "fetch"), **kw}


def write_day(folder, batches, extra_lines=()):
    path = folder / DAY
    path.mkdir(parents=True)
    with open(path / "batches.ndjson", "w", encoding="utf-8") as handle:
        for ip, seat, client, records, dropped in batches:
            handle.write(json.dumps({
                "received_at": "2026-10-01T04:00:00+00:00", "ip": ip, "user": "shared",
                "batch": {"v": 1, "client": client, "seat": seat, "page": "/app.html",
                          "env": {"ect": "4g", "rtt": 50}, "dropped": dropped, "records": records},
            }) + "\n")
        for line in extra_lines:
            handle.write(line + "\n")


@pytest.fixture
def day(tmp_path):
    """Two seats.

    Seat A (wired): Tasks page renders 10 x 9 MB thumbnails, then opens task 7
    whose image was one of them (cache hit), then task 8 (a 10 MB download).
    Seat B (wifi): slower heartbeats, one timeout, one probe pair.
    """
    a = [
        *[rec(i * 100, "image_list", it="img", p=f"/uploads/{i:032x}.jpg", tx=9_000_000,
              dl=900.0, ttfb=20.0, dur=920.0, rt="#/tasks") for i in range(10)],
        # open task 7: drain save ends 0.5 s before detail, image cached
        rec(5_000, "task_save", m="POST", dur=200.0, srv=150.0, ttfb=180.0, dl=5.0, tx=300),
        rec(5_700, "task_detail", m="GET", id="7", dur=100.0, srv=60.0, ttfb=80.0, dl=10.0, tx=40_000, stall=1.0),
        rec(5_810, "lock", m="POST", p="/api/tasks/{id}/claim", id="7", dur=10.0, srv=2.0, ttfb=6.0, dl=1.0, tx=300),
        rec(5_830, "image", it="img", p=f"/uploads/{7:032x}.jpg", tx=0, dl=0.0, dur=1.0),
        # open task 8: fresh download, 10 MB in 800 ms = 100 Mbps
        rec(20_000, "task_detail", m="GET", id="8", dur=90.0, srv=50.0, ttfb=70.0, dl=10.0, tx=50_000),
        rec(20_100, "lock", m="POST", p="/api/tasks/{id}/claim", id="8", dur=10.0, srv=2.0, ttfb=6.0, dl=1.0, tx=300),
        rec(20_120, "image", it="img", p="/uploads/" + "8" * 32 + ".jpg", tx=10_000_000,
            dl=800.0, ttfb=5.0, dur=810.0),
        *[rec(30_000 + i * 30_000, "heartbeat", m="POST", srv=1.0, ttfb=2.0, dl=0.2, dur=2.5, tx=300,
              rid=f"a{i}") for i in range(5)],
        rec(40_000, "telemetry", m="POST", dur=5.0, tx=100),
        rec(41_000, "heartbeat", m="POST", srv=1.0, ttfb=500.0, dur=500.0, tx=300, h=1),   # hidden page
        rec(42_000, "task_save", it="beacon", m="POST", dur=1.0),
    ]
    b = [
        *[rec(10_000 + i * 30_000, "heartbeat", m="POST", srv=1.0, ttfb=21.0, dl=0.5, dur=22.0, tx=300)
          for i in range(5)],
        rec(12_000, "task_detail", m="GET", id="9", err="timeout", dur=45_000.0),
        rec(13_000, "probe_down", m="GET", tx=4_000_000, dl=640.0, ttfb=20.0, srv=1.0, dur=660.0),
        rec(14_000, "probe_up", m="POST", req=1_000_000, srv=400.0, ttfb=420.0, dl=0.1, dur=421.0, tx=300),
    ]
    write_day(day_dir := tmp_path / "telemetry",
              [("10.0.0.1", "A", "client-a", a, 0), ("10.0.0.2", None, "client-b", b, 3)],
              extra_lines=["{broken json", json.dumps({"no": "batch"})])
    svc = tmp_path / "service" / DAY
    svc.mkdir(parents=True)
    (svc / "POST.log").write_text(
        "2026-10-01T10:00:00.000+05:45 INFO  POST /api/tasks/7/heartbeat 200 3ms user=x ip=10.0.0.1 req=a0\n"
        "garbage line\n", encoding="utf-8")
    inv = tmp_path / "inventory.csv"
    inv.write_text("ip,seat,wired|wifi,link_speed\n10.0.0.2,B-7,wifi,144\n", encoding="utf-8")
    return tmp_path, day_dir


def _ctx(day, **kw):
    root, telemetry_dir = day
    args = type("A", (), dict(date=DAY, to=None, dir=str(telemetry_dir),
                              service_dir=str(root / "service"), inventory=str(root / "inventory.csv"),
                              server_link_mbps=100, **kw))
    return tr.build(args)


def test_load_is_tolerant_and_counts(day):
    ctx = _ctx(day)
    s = ctx["stats"]
    assert s["batches"] == 2
    assert s["bad_lines"] == 2          # broken json + a line without a batch
    assert s["dropped"] == 3
    assert s["records"] == 33          # 25 on seat A, 8 on seat B


def test_seat_falls_back_to_inventory_then_ip(day):
    seats = {r["seat"] for r in _ctx(day)["rows"]}
    assert seats == {"A", "B-7"}


def test_network_split_is_ttfb_minus_server(day):
    hb = [r for r in _ctx(day)["rows"] if r["cat"] == "heartbeat" and r["seat"] == "B-7"]
    assert all(r["net_rtt"] == 20.0 for r in hb)


def test_category_summary_excludes_noise(day):
    cats = {c["cat"]: c for c in _ctx(day)["categories"]}
    assert "telemetry" not in cats
    hb = cats["heartbeat"]
    # 10 real heartbeats + 1 hidden-page one counted in n, not in latency.
    assert hb["n"] == 11
    assert hb["dur_p90"] < 100
    assert cats["task_detail"]["timeouts"] == 1
    assert cats["image"]["cache_hits"] == 1
    assert cats["image_list"]["mb"] == pytest.approx(90.0)


def test_task_open_reconstruction(day):
    opens = sorted(_ctx(day)["opens"], key=lambda o: o["t"])
    assert [o["task"] for o in opens] == ["7", "8"]
    first, second = opens
    # Starts at the drain save (ended 500 ms before the detail fetch).
    assert first["t"] == T0 + 5_000
    assert first["drain"] == 200.0 and first["image_cached"] is True
    assert first["total"] == pytest.approx(5_831 - 5_000)
    # No drain: starts at the detail fetch, ends when the image finished.
    assert second["drain"] == 0
    assert second["total"] == pytest.approx(20_120 + 810 - 20_000)
    assert second["claim"] == 10.0 and second["image_tx"] == 10_000_000


def test_timed_out_detail_is_not_an_open(day):
    assert all(o["seat"] != "B-7" for o in _ctx(day)["opens"])


def test_seat_summary_throughputs(day):
    seats = {s["seat"]: s for s in _ctx(day)["seats"]}
    b = seats["B-7"]
    assert b["link"] == "wifi" and b["link_speed"] == "144"
    assert b["rtt_p50"] == 20.0
    assert b["probe_down_mbps"] == pytest.approx(4_000_000 * 8 / 640 / 1000)   # 50 Mbps
    assert b["probe_up_mbps"] == pytest.approx(1_000_000 * 8 / 400 / 1000)     # 20 Mbps
    assert b["errors"] == 1
    a = seats["A"]
    assert a["img_mbps_p50"] == pytest.approx(80.0)   # 9 MB in 900 ms, 10 MB in 800 ms -> 80/100
    assert seats["B-7"]["rtt_p90"] >= seats["A"]["rtt_p90"]
    assert list(seats)[0] == "B-7"                    # worst RTT first


def test_list_view_analysis(day):
    lv = _ctx(day)["lists"]
    assert lv["renders"] == 1
    assert lv["mb_per_render_p50"] == pytest.approx(90.0)
    assert lv["list_share"] == pytest.approx(90 / 100)
    assert lv["canvas_cache_hits"] == 1
    assert lv["prefetched_by_list"] == 1


def test_service_log_join(day):
    joined = [r for r in _ctx(day)["rows"] if r.get("srv_log_ms") is not None]
    assert [(r["rid"], r["srv_log_ms"]) for r in joined] == [("a0", 3)]


def test_saturation(day):
    sat = _ctx(day)["saturation"]
    assert sat["link_mbps"] == 100
    assert sat["peak_mbps"] > 10          # ~100 MB within one minute
    assert sat["peak_utilisation"] == pytest.approx(sat["peak_mbps"] / 100)


def test_whatif_model_check_and_scenarios(day):
    rows = {w["scenario"]: w for w in _ctx(day)["whatif"]}
    assert rows["observed"]["open_p50"] is not None
    assert rows["today (model check)"]["open_p50"] is not None
    faster = rows["server link 2.5 Gbps, all wired"]["open_p90"]
    slower = rows["server link 100 Mbps, all wired"]["open_p90"]
    assert faster < slower
    assert rows["server link 2.5 Gbps, all wired"]["hours_saved"] >= 0


def test_main_writes_outputs_and_quick_mode(day, tmp_path, capsys):
    root, telemetry_dir = day
    out = tmp_path / "out"
    code = tr.main(["--date", DAY, "--dir", str(telemetry_dir), "--service-dir", str(root / "service"),
                    "--inventory", str(root / "inventory.csv"), "--server-link-mbps", "100",
                    "--out", str(out)])
    assert code == 0
    html_text = (out / "report.html").read_text(encoding="utf-8")
    for heading in ("Office summary", "Per seat", "Time of day", "Saturation", "Task opens",
                    "Tasks/Move page images", "What-if"):
        assert heading in html_text
    assert "<script" not in html_text        # self-contained, no external assets
    assert (out / "records.csv").read_text(encoding="utf-8").count("\n") == 34   # header + 33
    assert (out / "summary.csv").exists()
    tr.main(["--date", DAY, "--dir", str(telemetry_dir), "--quick"])
    assert "2 batches" in capsys.readouterr().out


def test_missing_day_is_reported_not_fatal(tmp_path):
    rows, stats = tr.load(str(tmp_path), DAY, "2026-10-02")
    assert rows == [] and stats["missing_days"] == 2
