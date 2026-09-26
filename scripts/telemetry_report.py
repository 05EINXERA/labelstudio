"""Turn a day (or range) of network telemetry into a report.

    python scripts/telemetry_report.py --date 2026-10-01
    python scripts/telemetry_report.py --date 2026-10-01 --to 2026-10-03 --out reports/w40
    python scripts/telemetry_report.py --date 2026-10-01 --quick
    python scripts/telemetry_report.py --date 2026-10-01 --inventory .devnotes/frontend-telemetry/data/inventory.csv \
        --server-link-mbps 1000

Reads `TELEMETRY_DIR/<date>/batches.ndjson` (written by api/routers/
telemetry.py) and writes, into --out:

  records.csv   one row per request, flattened, for Excel/pandas
  summary.csv   the per-category table
  report.html   every view in .devnotes/frontend-telemetry/05_ANALYSIS.md §4,
                self-contained (no external assets)

Standard library only: this runs on the deploy box without new dependencies
(CLAUDE.md rule 22). Tolerant by design: a malformed line or record is
skipped and counted, never fatal, because the collector is deliberately not
validated field by field at ingest.

Temporary tooling for .devnotes/frontend-telemetry/; it has no runtime
footprint and may be kept after the feature is reverted (06 §2).
"""
import argparse
import csv
import html
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config  # noqa: E402

# Records that describe the telemetry itself, or that cannot carry a latency.
_EXCLUDED_CATS = {"telemetry"}
# Below this a download is dominated by latency; throughput is meaningless.
MIN_THROUGHPUT_BYTES = 256 * 1024
# A task open's pieces must follow the detail fetch within this window.
OPEN_WINDOW_MS = 30_000
# A drain save ending this close before the detail fetch belongs to the open.
DRAIN_GAP_MS = 1_000
# Serial round trips in a task open: drain, detail, claim, image request
# (01_TRAFFIC_INVENTORY.md §2.2).
SERIAL_RTTS = 4

RECORD_FIELDS = [
    "received_at", "ip", "user", "client", "seat", "page", "t", "it", "m", "cat", "p", "id",
    "rt", "st", "err", "dns", "tcp", "stall", "ttfb", "dl", "dur", "srv", "hdr", "net_rtt",
    "tx", "enc", "dec", "req", "rid", "srv_log_ms", "h", "proto", "ect", "env_rtt", "env_down",
]


# --- loading -----------------------------------------------------------------

def _dates(start: str, end: str):
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end or start)
    while d0 <= d1:
        yield d0.isoformat()
        d0 += timedelta(days=1)


def load(telemetry_dir: str, start: str, end: str = None, inventory: dict = None):
    """Flatten batches into rows. Returns (rows, stats)."""
    rows, stats = [], Counter()
    inventory = inventory or {}
    for day in _dates(start, end):
        path = os.path.join(telemetry_dir, day, "batches.ndjson")
        if not os.path.isfile(path):
            stats["missing_days"] += 1
            continue
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    wrapper = json.loads(line)
                    batch = wrapper["batch"]
                    records = batch["records"]
                except (ValueError, KeyError, TypeError):
                    stats["bad_lines"] += 1
                    continue
                stats["batches"] += 1
                stats["dropped"] += int(batch.get("dropped") or 0)
                env = batch.get("env") or {}
                ip = wrapper.get("ip")
                seat = batch.get("seat") or (inventory.get(ip) or {}).get("seat") or ip
                for rec in records:
                    if not isinstance(rec, dict):
                        stats["bad_records"] += 1
                        continue
                    row = {k: rec.get(k) for k in RECORD_FIELDS if k in rec}
                    row.update(
                        received_at=wrapper.get("received_at"), ip=ip, user=wrapper.get("user"),
                        client=batch.get("client"), seat=seat, page=batch.get("page"),
                        ect=env.get("ect"), env_rtt=env.get("rtt"), env_down=env.get("down"),
                    )
                    if _num(row.get("ttfb")) is not None and _num(row.get("srv")) is not None:
                        row["net_rtt"] = max(0.0, row["ttfb"] - row["srv"])
                    rows.append(row)
                    stats["records"] += 1
    rows.sort(key=lambda r: (_num(r.get("t")) or 0))
    return rows, stats


def load_inventory(path: str) -> dict:
    """ip -> inventory row (04_TEST_PROTOCOL.md §1.3)."""
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8", newline="") as handle:
        return {r["ip"].strip(): r for r in csv.DictReader(handle) if r.get("ip")}


_SERVICE_LINE = re.compile(
    r"^(?P<ts>\S+) \S+\s+(?P<method>[A-Z]+) (?P<path>\S+) (?P<status>\d{3}) (?P<ms>\d+)ms .*?\breq=(?P<rid>\S+)")


def load_service_log(service_dir: str, start: str, end: str = None):
    """rid -> server-logged ms, and a per-second request count (a concurrency
    proxy). Joins client records to the server's own view (05 §5)."""
    by_rid, per_second = {}, Counter()
    for day in _dates(start, end):
        folder = os.path.join(service_dir, day)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.endswith(".log") or name == "errors.log":
                continue
            with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    m = _SERVICE_LINE.match(line)
                    if not m:
                        continue
                    by_rid[m["rid"]] = int(m["ms"])
                    per_second[m["ts"][:19]] += 1
    return by_rid, per_second


def join_service_log(rows, by_rid):
    for row in rows:
        rid = row.get("rid")
        if rid and rid in by_rid:
            row["srv_log_ms"] = by_rid[rid]


# --- helpers -------------------------------------------------------------------

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def pct(values, q):
    vals = sorted(v for v in values if _num(v) is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def mbps(nbytes, ms):
    if not nbytes or not ms or ms <= 0:
        return None
    return nbytes * 8 / (ms * 1000)


def is_latency_row(r):
    """Rows that carry a meaningful response time (05 §1)."""
    return (
        r.get("cat") not in _EXCLUDED_CATS
        and r.get("it") != "beacon"
        and not r.get("h")
        and r.get("tx") != 0          # cache hits have no network time
        and not r.get("err")
    )


def _fmt(v, digits=1):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:,.{digits}f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


# --- views ---------------------------------------------------------------------

def category_summary(rows):
    groups = defaultdict(list)
    for r in rows:
        if r.get("cat") not in _EXCLUDED_CATS:
            groups[r.get("cat") or "other"].append(r)
    out = []
    for cat, rs in groups.items():
        lat = [r for r in rs if is_latency_row(r)]
        shares = []
        for r in lat:
            dur = _num(r.get("dur"))
            if dur and r.get("net_rtt") is not None:
                network = (_num(r.get("tcp")) or 0) + r["net_rtt"] + (_num(r.get("dl")) or 0)
                shares.append(min(1.0, network / dur))
        out.append({
            "cat": cat,
            "n": len(rs),
            "cache_hits": sum(1 for r in rs if r.get("tx") == 0),
            "dur_p50": pct([r.get("dur") for r in lat], .5),
            "dur_p90": pct([r.get("dur") for r in lat], .9),
            "dur_p99": pct([r.get("dur") for r in lat], .99),
            "srv_p50": pct([r.get("srv") for r in lat], .5),
            "srv_p90": pct([r.get("srv") for r in lat], .9),
            "net_rtt_p50": pct([r.get("net_rtt") for r in lat], .5),
            "net_rtt_p90": pct([r.get("net_rtt") for r in lat], .9),
            "stall_p90": pct([r.get("stall") for r in lat], .9),
            "network_share": statistics.mean(shares) if shares else None,
            "errors": sum(1 for r in rs if r.get("err") in ("neterr", "timeout")),
            "timeouts": sum(1 for r in rs if r.get("err") == "timeout"),
            "http_5xx": sum(1 for r in rs if (_num(r.get("st")) or 0) >= 500),
            "mb": sum(_num(r.get("tx")) or 0 for r in rs) / 1e6,
        })
    out.sort(key=lambda x: -x["n"])
    return out


def task_opens(rows):
    """Reconstruct task opens per client (05 §3)."""
    by_client = defaultdict(list)
    for r in rows:
        by_client[r.get("client")].append(r)
    opens = []
    for client, rs in by_client.items():
        rs.sort(key=lambda r: _num(r.get("t")) or 0)
        for i, d in enumerate(rs):
            if d.get("cat") != "task_detail" or d.get("err") or _num(d.get("t")) is None:
                continue
            t0 = d["t"]
            tid = d.get("id")
            claim = image = None
            for r in rs[i + 1:]:
                if (_num(r.get("t")) or 0) - t0 > OPEN_WINDOW_MS:
                    break
                if claim is None and r.get("cat") == "lock" and r.get("m") == "POST" \
                        and r.get("id") == tid and str(r.get("p", "")).endswith("/claim"):
                    claim = r
                elif image is None and r.get("cat") == "image":
                    image = r
                if claim and image:
                    break
            if image is None:
                continue
            drain = None
            for r in reversed(rs[:i]):
                if r.get("cat") == "task_save" and _num(r.get("t")) is not None:
                    end = r["t"] + (_num(r.get("dur")) or 0)
                    if 0 <= t0 - end <= DRAIN_GAP_MS:
                        drain = r
                    break
            start = drain["t"] if drain else t0
            end = image["t"] + (_num(image.get("dur")) or 0)
            opens.append({
                "client": client, "seat": d.get("seat"), "t": start, "task": tid,
                "total": end - start,
                "drain": _num(drain.get("dur")) if drain else 0,
                "detail": _num(d.get("dur")) or 0,
                "detail_srv": _num(d.get("srv")) or 0,
                "detail_tx": _num(d.get("tx")) or 0,
                "claim": _num(claim.get("dur")) if claim else 0,
                "claim_srv": _num(claim.get("srv")) if claim else 0,
                "drain_srv": _num(drain.get("srv")) if drain else 0,
                "image": _num(image.get("dur")) or 0,
                "image_tx": _num(image.get("tx")) or 0,
                "image_cached": image.get("tx") == 0,
                "stall": sum(_num(x.get("stall")) or 0 for x in (d, claim, image, drain) if x),
            })
    return opens


def seat_summary(rows, opens, inventory_by_seat):
    seats = defaultdict(list)
    for r in rows:
        seats[r.get("seat")].append(r)
    open_by_seat = defaultdict(list)
    for o in opens:
        open_by_seat[o["seat"]].append(o["total"])
    out = []
    for seat, rs in seats.items():
        hb = [r.get("net_rtt") for r in rs if r.get("cat") in ("heartbeat", "time_sync") and is_latency_row(r)]
        img = [mbps(r.get("tx"), r.get("dl")) for r in rs
               if r.get("cat") in ("image", "image_list") and (_num(r.get("tx")) or 0) >= MIN_THROUGHPUT_BYTES]
        down = [mbps(r.get("tx"), r.get("dl")) for r in rs if r.get("cat") == "probe_down"]
        up = [mbps(r.get("req"), r.get("srv")) for r in rs if r.get("cat") == "probe_up"]
        inv = inventory_by_seat.get(seat, {})
        out.append({
            "seat": seat,
            "ip": next((r.get("ip") for r in rs if r.get("ip")), None),
            "link": inv.get("wired|wifi") or inv.get("link") or "",
            "link_speed": inv.get("link_speed", ""),
            "requests": len(rs),
            "rtt_p50": pct(hb, .5), "rtt_p90": pct(hb, .9),
            "img_mbps_p50": pct(img, .5),
            "probe_down_mbps": pct(down, .5), "probe_up_mbps": pct(up, .5),
            "open_p50": pct(open_by_seat[seat], .5), "open_p90": pct(open_by_seat[seat], .9),
            "errors": sum(1 for r in rs if r.get("err") in ("neterr", "timeout")),
        })
    out.sort(key=lambda x: -(x["rtt_p90"] or 0))
    return out


def _bucket(t_ms, minutes):
    dt = datetime.fromtimestamp(t_ms / 1000).astimezone()
    return dt.replace(minute=dt.minute - dt.minute % minutes, second=0, microsecond=0)


def time_of_day(rows, opens, minutes=5):
    buckets = defaultdict(lambda: {"clients": set(), "img": 0, "list": 0, "rtt": [], "open": []})
    for r in rows:
        t = _num(r.get("t"))
        if t is None:
            continue
        b = buckets[_bucket(t, minutes)]
        b["clients"].add(r.get("client"))
        if r.get("cat") == "image":
            b["img"] += _num(r.get("tx")) or 0
        elif r.get("cat") == "image_list":
            b["list"] += _num(r.get("tx")) or 0
        if r.get("cat") in ("heartbeat", "time_sync") and is_latency_row(r) and r.get("net_rtt") is not None:
            b["rtt"].append(r["net_rtt"])
    for o in opens:
        buckets[_bucket(o["t"], minutes)]["open"].append(o["total"])
    secs = minutes * 60
    return [{
        "bucket": k.strftime("%Y-%m-%d %H:%M"),
        "clients": len(v["clients"]),
        "canvas_img_mbps": v["img"] * 8 / secs / 1e6,
        "list_img_mbps": v["list"] * 8 / secs / 1e6,
        "rtt_p90": pct(v["rtt"], .9),
        "open_p50": pct(v["open"], .5),
    } for k, v in sorted(buckets.items())]


def saturation(rows, link_mbps=None):
    """Office-wide bytes per 1-min bucket against the server link (05 §4.4)."""
    per_min = Counter()
    for r in rows:
        t = _num(r.get("t"))
        if t is not None and r.get("cat") not in _EXCLUDED_CATS:
            per_min[_bucket(t, 1)] += (_num(r.get("tx")) or 0) + (_num(r.get("req")) or 0)
    if not per_min:
        return None
    peak_at, peak = max(per_min.items(), key=lambda kv: kv[1])
    peak_mbps = peak * 8 / 60 / 1e6
    return {
        "peak_minute": peak_at.strftime("%Y-%m-%d %H:%M"),
        "peak_mbps": peak_mbps,
        "p90_minute_mbps": pct([v * 8 / 60 / 1e6 for v in per_min.values()], .9),
        "link_mbps": link_mbps,
        "peak_utilisation": (peak_mbps / link_mbps) if link_mbps else None,
    }


def list_views(rows):
    """Tasks/Move page images (01 §3, 05 §4.9)."""
    renders = []
    by_client = defaultdict(list)
    for r in rows:
        if r.get("cat") == "image_list" and _num(r.get("t")) is not None:
            by_client[r.get("client")].append(r)
    for rs in by_client.values():
        rs.sort(key=lambda r: r["t"])
        current = None
        for r in rs:
            # One page render = a burst of list images within 2 s of each other.
            if current and r["t"] - current["last"] <= 2000:
                current["bytes"] += _num(r.get("tx")) or 0
                current["images"] += 1
                current["last"] = r["t"]
            else:
                current = {"bytes": _num(r.get("tx")) or 0, "images": 1, "last": r["t"], "rt": r.get("rt")}
                renders.append(current)
    list_bytes = sum(_num(r.get("tx")) or 0 for r in rows if r.get("cat") == "image_list")
    canvas_bytes = sum(_num(r.get("tx")) or 0 for r in rows if r.get("cat") == "image")
    thumb_bytes = sum(_num(r.get("tx")) or 0 for r in rows if r.get("cat") == "thumb")

    # Canvas opens served from cache because the list view fetched the image.
    seen_in_list = defaultdict(set)
    prefetched = cached_canvas = 0
    for r in rows:
        if r.get("cat") == "image_list":
            seen_in_list[r.get("client")].add(r.get("p"))
        elif r.get("cat") == "image" and r.get("tx") == 0:
            cached_canvas += 1
            if r.get("p") in seen_in_list[r.get("client")]:
                prefetched += 1
    canvas_opens = sum(1 for r in rows if r.get("cat") == "image")
    return {
        "renders": len(renders),
        "mb_per_render_p50": (pct([x["bytes"] for x in renders], .5) or 0) / 1e6,
        "mb_per_render_max": max((x["bytes"] for x in renders), default=0) / 1e6,
        "list_mb": list_bytes / 1e6,
        "canvas_mb": canvas_bytes / 1e6,
        "thumb_mb": thumb_bytes / 1e6,
        "list_share": list_bytes / (list_bytes + canvas_bytes) if (list_bytes + canvas_bytes) else None,
        "canvas_opens": canvas_opens,
        "canvas_cache_hits": cached_canvas,
        "prefetched_by_list": prefetched,
    }


def whatif(opens, rows, scenarios):
    """Recompute each observed open under hypothetical RTT/bandwidth (05 §6).

    Kept fixed: server time and browser stall. Changed: RTT per serial round
    trip, and transfer time at the scenario's per-seat bandwidth share, where
    the share divides the link by the clients downloading images in the same
    minute (with or without the list-view downloaders, per scenario).
    """
    downloaders = defaultdict(set)
    list_downloaders = defaultdict(set)
    for r in rows:
        t = _num(r.get("t"))
        if t is None or not (_num(r.get("tx")) or 0):
            continue
        if r.get("cat") == "image":
            downloaders[_bucket(t, 1)].add(r.get("client"))
        elif r.get("cat") == "image_list":
            list_downloaders[_bucket(t, 1)].add(r.get("client"))

    # "today" uses each seat's own measured RTT and image throughput.
    seat_rtt = defaultdict(list)
    seat_bw = defaultdict(list)
    for r in rows:
        if r.get("cat") in ("heartbeat", "time_sync") and r.get("net_rtt") is not None and is_latency_row(r):
            seat_rtt[r.get("seat")].append(r["net_rtt"])
        if r.get("cat") in ("image", "image_list") and (_num(r.get("tx")) or 0) >= MIN_THROUGHPUT_BYTES:
            seat_bw[r.get("seat")].append(mbps(r["tx"], r.get("dl")))

    results = []
    for sc in scenarios:
        modelled, saved_ms = [], 0.0
        for o in opens:
            bucket = _bucket(o["t"], 1)
            srv = o["drain_srv"] + o["detail_srv"] + o["claim_srv"]
            nbytes = o["detail_tx"] + o["image_tx"]
            if sc["name"] == "today (model check)":
                rtt = pct(seat_rtt[o["seat"]], .5) or 0
                bw = pct([b for b in seat_bw[o["seat"]] if b], .5)
            else:
                rtt = sc["rtt_ms"]
                conc = set(downloaders[bucket])
                if not sc.get("without_list"):
                    conc |= list_downloaders[bucket]
                bw = sc["link_mbps"] / max(1, len(conc))
            transfer = (nbytes * 8 / (bw * 1000)) if bw else 0
            t_new = srv + SERIAL_RTTS * rtt + transfer + o["stall"]
            modelled.append(t_new)
            saved_ms += max(0.0, o["total"] - t_new)
        results.append({
            "scenario": sc["name"],
            "open_p50": pct(modelled, .5), "open_p90": pct(modelled, .9),
            "hours_saved": saved_ms / 3.6e6 if sc["name"] != "today (model check)" else None,
        })
    observed = {"scenario": "observed", "open_p50": pct([o["total"] for o in opens], .5),
                "open_p90": pct([o["total"] for o in opens], .9), "hours_saved": None}
    return [observed] + results


def default_scenarios(link_mbps):
    return [
        {"name": "today (model check)"},
        {"name": f"server link {link_mbps:g} Mbps, all wired", "rtt_ms": 0.3, "link_mbps": link_mbps},
        {"name": "server link 1 Gbps, all wired", "rtt_ms": 0.3, "link_mbps": 1000},
        {"name": "server link 2.5 Gbps, all wired", "rtt_ms": 0.3, "link_mbps": 2500},
        {"name": f"real thumbnails (07), link {link_mbps:g} Mbps", "rtt_ms": 0.3,
         "link_mbps": link_mbps, "without_list": True},
    ]


# --- output --------------------------------------------------------------------

def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def _table(title, rows, cols, note=""):
    if not rows:
        return f"<h2>{html.escape(title)}</h2><p class=note>No data.</p>"
    head = "".join(f"<th>{html.escape(label)}</th>" for _, label in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(_fmt(r.get(key)))}</td>" for key, _ in cols) + "</tr>"
        for r in rows)
    note = f"<p class=note>{html.escape(note)}</p>" if note else ""
    return f"<h2>{html.escape(title)}</h2>{note}<div class=scroll><table><tr>{head}</tr>{body}</table></div>"


def _sparkline(points, key, label):
    vals = [p.get(key) for p in points]
    nums = [v for v in vals if v is not None]
    if len(nums) < 2:
        return ""
    w, h, top = 720, 120, max(nums) or 1
    step = w / max(1, len(vals) - 1)
    path = " ".join(
        f"{'M' if i == 0 or vals[i - 1] is None else 'L'}{i * step:.1f},{h - (v / top) * (h - 10):.1f}"
        for i, v in enumerate(vals) if v is not None)
    return (f"<figure><figcaption>{html.escape(label)} (max {_fmt(top)})</figcaption>"
            f"<svg viewBox='0 0 {w} {h}' role=img aria-label='{html.escape(label)}'>"
            f"<path d='{path}' fill=none stroke=currentColor stroke-width=2 /></svg></figure>")


def render_html(ctx):
    s = ctx["stats"]
    parts = [
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
        f"<title>Network report {html.escape(ctx['period'])}</title>",
        "<style>:root{--fg:#1d1d1f;--bg:#fff;--line:#ddd;--muted:#666}"
        "@media (prefers-color-scheme:dark){:root{--fg:#eee;--bg:#161618;--line:#333;--muted:#999}}"
        "body{font:14px/1.45 system-ui,sans-serif;color:var(--fg);background:var(--bg);margin:0 auto;"
        "max-width:1100px;padding:16px}table{border-collapse:collapse;font-variant-numeric:tabular-nums}"
        "th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right;white-space:nowrap}"
        "th:first-child,td:first-child{text-align:left}.scroll{overflow-x:auto}.note{color:var(--muted)}"
        "svg{width:100%;height:auto;color:#3b82f6}figure{margin:12px 0}</style>",
        f"<h1>Network report — {html.escape(ctx['period'])}</h1>",
        f"<p class=note>{s['batches']} batches, {s['records']} records, {s['dropped']} dropped by clients, "
        f"{s['bad_lines']} bad lines, {s['missing_days']} missing days. "
        "Method: .devnotes/frontend-telemetry/05_ANALYSIS.md.</p>",
        _table("1. Office summary by request type", ctx["categories"], [
            ("cat", "category"), ("n", "n"), ("cache_hits", "cache hits"),
            ("dur_p50", "dur p50 ms"), ("dur_p90", "p90"), ("dur_p99", "p99"),
            ("srv_p50", "server p50"), ("srv_p90", "server p90"),
            ("net_rtt_p50", "net p50"), ("net_rtt_p90", "net p90"), ("stall_p90", "stall p90"),
            ("network_share", "network share"), ("errors", "net err"), ("timeouts", "timeouts"),
            ("http_5xx", "5xx"), ("mb", "MB")],
            "net = TTFB − X-Server-Ms. stall = queued in the browser (connection limit), not network."),
        _table("2. Per seat (worst heartbeat RTT first)", ctx["seats"], [
            ("seat", "seat"), ("ip", "ip"), ("link", "link"), ("link_speed", "speed"),
            ("requests", "requests"), ("rtt_p50", "RTT p50 ms"), ("rtt_p90", "RTT p90"),
            ("img_mbps_p50", "image Mbps"), ("probe_down_mbps", "probe ↓ Mbps"),
            ("probe_up_mbps", "probe ↑ Mbps"), ("open_p50", "open p50 ms"),
            ("open_p90", "open p90"), ("errors", "errors")]),
        "<h2>3. Time of day</h2><p class=note>If RTT and task-open rise with office-wide Mbit/s, "
        "a shared link saturates. If they stay flat while single seats are slow, the problem is per seat.</p>",
        _sparkline(ctx["tod"], "clients", "Active clients"),
        _sparkline(ctx["tod"], "list_img_mbps", "Tasks/Move page image Mbit/s (office)"),
        _sparkline(ctx["tod"], "canvas_img_mbps", "Canvas image Mbit/s (office)"),
        _sparkline(ctx["tod"], "rtt_p90", "Heartbeat RTT p90 ms"),
        _sparkline(ctx["tod"], "open_p50", "Task open p50 ms"),
        _table("Time-of-day buckets", ctx["tod"], [
            ("bucket", "bucket"), ("clients", "clients"), ("list_img_mbps", "list img Mbps"),
            ("canvas_img_mbps", "canvas img Mbps"), ("rtt_p90", "RTT p90"), ("open_p50", "open p50")]),
        _table("4. Saturation", [ctx["saturation"]] if ctx["saturation"] else [], [
            ("peak_minute", "peak minute"), ("peak_mbps", "peak Mbps"),
            ("p90_minute_mbps", "p90 minute Mbps"), ("link_mbps", "server link Mbps"),
            ("peak_utilisation", "peak utilisation")],
            "Above ~0.6-0.7 utilisation in 1-minute buckets, the server link is the bottleneck. "
            "Pass --server-link-mbps from the inventory."),
        _table("5. Task opens", ctx["open_summary"], [
            ("part", "part"), ("p50", "p50 ms"), ("p90", "p90 ms")],
            f"{len(ctx['opens'])} opens reconstructed from task_detail → claim → image."),
        _table("6. Tasks/Move page images (I1)", [ctx["lists"]], [
            ("renders", "page renders"), ("mb_per_render_p50", "MB per render p50"),
            ("mb_per_render_max", "max"), ("list_mb", "list MB"), ("canvas_mb", "canvas MB"),
            ("thumb_mb", "thumb MB"), ("list_share", "list share of image bytes"),
            ("canvas_opens", "canvas image loads"), ("canvas_cache_hits", "from cache"),
            ("prefetched_by_list", "prefetched by list")]),
        _table("7. What-if: task open under other networks", ctx["whatif"], [
            ("scenario", "scenario"), ("open_p50", "open p50 ms"), ("open_p90", "p90 ms"),
            ("hours_saved", "annotator-hours saved")],
            "'today (model check)' must be within ~10% of 'observed' before any other row is trusted."),
    ]
    return "\n".join(p for p in parts if p)


def open_summary(opens):
    parts = [("total", "total"), ("drain", "drain save"), ("detail", "detail"),
             ("detail_srv", "detail (server)"), ("claim", "claim"), ("image", "image"),
             ("stall", "browser stall")]
    return [{"part": label, "p50": pct([o[k] for o in opens], .5), "p90": pct([o[k] for o in opens], .9)}
            for k, label in parts]


def build(args):
    inventory = load_inventory(args.inventory)
    rows, stats = load(args.dir, args.date, args.to, inventory)
    by_rid, _per_second = load_service_log(args.service_dir, args.date, args.to)
    join_service_log(rows, by_rid)
    inventory_by_seat = {(r.get("seat") or ip): r for ip, r in inventory.items()}
    opens = task_opens(rows)
    link = args.server_link_mbps or 1000
    return {
        "period": args.date + (f" to {args.to}" if args.to else ""),
        "stats": stats, "rows": rows, "opens": opens,
        "categories": category_summary(rows),
        "seats": seat_summary(rows, opens, inventory_by_seat),
        "tod": time_of_day(rows, opens),
        "saturation": saturation(rows, args.server_link_mbps),
        "lists": list_views(rows),
        "open_summary": open_summary(opens),
        "whatif": whatif(opens, rows, default_scenarios(link)),
    }


def quick(ctx):
    s = ctx["stats"]
    lines = [f"{ctx['period']}: {s['batches']} batches, {s['records']} records, "
             f"{s['dropped']} dropped, {s['bad_lines']} bad lines"]
    per_seat = Counter(r.get("seat") for r in ctx["rows"])
    for seat, n in sorted(per_seat.items(), key=lambda kv: str(kv[0])):
        lines.append(f"  seat {seat}: {n} records")
    for c in ctx["categories"][:12]:
        lines.append(f"  {c['cat']:<16} n={c['n']:<6} p50={_fmt(c['dur_p50'])}ms MB={_fmt(c['mb'])}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD (local date folder)")
    ap.add_argument("--to", help="last date of a range, inclusive")
    ap.add_argument("--dir", default=config.TELEMETRY_DIR, help="telemetry root (default: TELEMETRY_DIR)")
    ap.add_argument("--service-dir", default=os.path.join(config.LOG_DIR, "service"),
                    help="service log root, for the request-id join")
    ap.add_argument("--inventory", help="inventory.csv (ip, seat, wired|wifi, link_speed, ...)")
    ap.add_argument("--server-link-mbps", type=float, help="server NIC link speed, for saturation")
    ap.add_argument("--out", help="output folder (default: <dir>/<date>/report)")
    ap.add_argument("--quick", action="store_true", help="print counts only")
    args = ap.parse_args(argv)

    ctx = build(args)
    if args.quick:
        print(quick(ctx))
        return 0
    out = args.out or os.path.join(args.dir, args.date, "report")
    os.makedirs(out, exist_ok=True)
    write_csv(os.path.join(out, "records.csv"), ctx["rows"], RECORD_FIELDS)
    write_csv(os.path.join(out, "summary.csv"), ctx["categories"],
              list(ctx["categories"][0].keys()) if ctx["categories"] else ["cat"])
    with open(os.path.join(out, "report.html"), "w", encoding="utf-8") as handle:
        handle.write(render_html(ctx))
    print(quick(ctx))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
