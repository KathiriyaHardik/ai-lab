#!/usr/bin/env python3
"""End-to-end test of the tracker using Microsoft Excel (macOS) as the calculation engine.

1. builds a copy of the workbook filled with dummy rows (build_tracker.sample_data)
2. opens it in Excel, reads the computed values back over AppleScript
3. compares them with an independent Python model of the tracker's rules
4. counts formula errors on every sheet (using Excel's own ISERROR)
5. repeats the error/zero check on the clean, empty workbook

Excel's sandbox blocks scripted saves, so nothing is written back: values are read live
and the workbooks are closed without saving.

    python verify_tracker.py
"""
import datetime as dt
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import build_tracker as bt

FAILS = []
CHECKED = [0]


# ----------------------------------------------------------------------------- Excel bridge
def osa(script, source_form=False):
    args = ["osascript"] + (["-s", "s"] if source_form else []) + ["-e", script]
    out = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if out.returncode:
        raise RuntimeError(out.stderr.strip())
    return out.stdout.strip()


def parse_applescript(s):
    """Parse AppleScript source-form output ({lists}, "strings", numbers, dates, missing value)."""
    pos = 0

    def skip():
        nonlocal pos
        while pos < len(s) and s[pos] in " \n\t,":
            pos += 1

    def string():
        nonlocal pos
        pos += 1
        out = []
        while s[pos] != '"':
            if s[pos] == "\\":
                pos += 1
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(s[pos], s[pos]))
            else:
                out.append(s[pos])
            pos += 1
        pos += 1
        return "".join(out)

    def value():
        nonlocal pos
        skip()
        if s[pos] == "{":
            pos += 1
            items = []
            skip()
            while s[pos] != "}":
                items.append(value())
                skip()
            pos += 1
            return items
        if s[pos] == '"':
            return string()
        if s.startswith("date ", pos):
            pos += 5
            text = string()
            m = re.search(r"(\d{1,2})\.? (\w+),? (\d{4})", text)
            return dt.datetime.strptime(" ".join(m.groups()), "%d %B %Y").date()
        for word, val in (("missing value", None), ("true", True), ("false", False)):
            if s.startswith(word, pos):
                pos += len(word)
                return val
        m = re.compile(r"-?\d+(\.\d+)?(E[-+]?\d+)?").match(s, pos)
        pos = m.end()
        return float(m.group())

    return value()


class Excel:
    def __init__(self, path):
        self.path, self.name = Path(path), Path(path).name
        subprocess.run(["open", "-a", "Microsoft Excel", str(self.path)], check=True)
        for _ in range(90):
            try:
                if self.name in osa('tell application "Microsoft Excel" to return name of every workbook'):
                    break
            except RuntimeError:
                pass
            time.sleep(2)
        else:
            raise RuntimeError(f"Excel did not open {self.name}")
        osa('tell application "Microsoft Excel" to calculate full')

    def _ws(self, sheet):
        return f'worksheet "{sheet}" of workbook "{self.name}"'

    def read(self, sheet, rng):
        return parse_applescript(osa(
            f'tell application "Microsoft Excel" to return value of range "{rng}" of {self._ws(sheet)}', True))

    def write(self, sheet, cell, value):
        osa(f'tell application "Microsoft Excel" to set value of range "{cell}" of {self._ws(sheet)} to {value}')

    def error_count(self, sheet, rng="A1:AZ1100", probe="BA1"):
        return parse_applescript(osa(f'''
            tell application "Microsoft Excel"
              set ws to {self._ws(sheet)}
              set formula of range "{probe}" of ws to "=SUMPRODUCT(--ISERROR({rng}))"
              set v to value of range "{probe}" of ws
              set formula of range "{probe}" of ws to ""
              return v
            end tell''', True))

    def close(self):
        osa(f'tell application "Microsoft Excel" to close workbook "{self.name}" saving no')


# ----------------------------------------------------------------------------- independent model
def model(data, today, fu=7, ds=3, iw=14, min_sample=5):
    stages = bt.PIPELINE
    apps = []
    for i, a in enumerate(data["apps"]):
        aid, co, title, status, applied, resp, nxt, closed_at, cv, role, source, match, skills = a
        if status in ("Rejected", "Withdrawn"):
            furthest = closed_at or ("Applied" if status == "Rejected" or applied else "Wishlist")
        else:
            furthest = status or ("Applied" if applied else "Wishlist")
        rank = stages.index(furthest)
        flag = ""
        if status not in bt.CLOSED:
            if nxt is not None:
                gap = (nxt - today).days
                flag = ("FOLLOW UP REQUIRED" if gap < 0 else "ACTION DUE TODAY" if gap == 0
                        else "DUE SOON" if gap <= ds else "")
            elif status == "Applied" and resp is None and applied and (today - applied).days >= fu:
                flag = "NO RESPONSE"
        band = ""
        if match is not None:
            band = next(b[0] for b in bt.MATCH_BANDS if match >= b[1])
        apps.append(dict(row=bt.FIRST + i, id=aid, co=co, title=title, status=status, applied=applied, resp=resp,
                         nxt=nxt, cv=cv, role=role, source=source, match=match, skills=skills or "",
                         furthest=furthest, rank=rank, flag=flag, band=band,
                         days=(resp - applied).days if resp and applied else None,
                         active=1 <= rank <= 6 and status not in ("Rejected", "Withdrawn")))
    sub = [a for a in apps if a["rank"] >= 1]
    monday = today - dt.timedelta(days=today.weekday())
    month0 = today.replace(day=1)
    m = {
        "total": len(sub),
        "week": sum(1 for a in apps if a["applied"] and monday <= a["applied"] < monday + dt.timedelta(7)),
        "month": sum(1 for a in apps if a["applied"] and a["applied"] >= month0 and a["applied"].month == today.month),
        "active": sum(a["active"] for a in apps),
        "ints": sum(a["rank"] >= 3 for a in apps),
        "finals": sum(a["rank"] >= 5 for a in apps),
        "offers": sum(a["rank"] >= 6 for a in apps),
        "accepted": sum(a["status"] == "Accepted" for a in apps),
    }
    rate = lambda n, d: n / d if d else "–"  # noqa: E731
    m.update(app_int=rate(m["ints"], m["total"]), int_final=rate(m["finals"], m["ints"]),
             final_offer=rate(m["offers"], m["finals"]), offer_rate=rate(m["offers"], m["total"]))
    days = [a["days"] for a in apps if a["days"] is not None]
    m["avg_days"] = sum(days) / len(days) if days else "–"
    m["pipeline"] = [sum(a["status"] == s for a in apps) for s in bt.LISTS["Status"]]
    m["missing_date"] = sum(1 for a in sub if not a["applied"])
    m["missing_role"] = sum(1 for a in sub if not a["role"])
    ids = [a["id"] for a in apps if a["id"] is not None]
    m["dup_ids"] = sum(1 for i in ids if ids.count(i) > 1)
    m["next_id"] = max(ids) + 1

    def breakdown(key, values):
        rows = [sum(1 for a in sub if a[key] == v) for v in values]
        return rows + [len(sub) - sum(rows)]
    m["role"] = breakdown("role", bt.LISTS["Role Category"])
    m["source"] = breakdown("source", bt.LISTS["Source"])
    m["match"] = breakdown("band", [b[0] for b in bt.MATCH_BANDS])

    def group(key, values):
        out = []
        for v in values:
            g = [a for a in sub if a[key] == v]
            out.append((len(g), sum(a["rank"] >= 3 for a in g), sum(a["rank"] >= 6 for a in g)))
        rest = (len(sub) - sum(o[0] for o in out), m["ints"] - sum(o[1] for o in out),
                m["offers"] - sum(o[2] for o in out))
        eligible = [(o[1] / o[0], i) for i, o in enumerate(out) if o[0] > 0 and o[0] >= min_sample]
        if not eligible:
            best = "Not enough data yet"
        else:
            r, i = max(eligible, key=lambda t: (t[0], -t[1]))
            best = "No interviews yet" if r == 0 else (
                f"{values[i]}  —  {round(r * 100)}% interview rate ({out[i][1]} of {out[i][0]})")
        return out + [rest], best
    m["cv"], m["cv_best"] = group("cv", bt.LISTS["CV Version"])
    m["an_role"], m["role_best"] = group("role", bt.LISTS["Role Category"])
    m["an_source"], m["source_best"] = group("source", bt.LISTS["Source"])
    m["an_match"], m["match_best"] = group("band", [b[0] for b in bt.MATCH_BANDS])

    def has_tag(a, tag):
        norm = "," + a["skills"].replace(" ", "").replace(";", ",").lower() + ","
        return "," + tag.replace(" ", "").lower() + "," in norm
    m["skills"] = [(sum(has_tag(a, t) for a in sub), sum(has_tag(a, t) and a["rank"] >= 3 for a in sub))
                   for t in bt.LISTS["Skill Tags"]]
    m["weekly"] = []
    for k in range(12):
        ws = monday - dt.timedelta(days=7 * (11 - k))
        we = ws + dt.timedelta(7)
        m["weekly"].append((ws, sum(1 for a in apps if a["applied"] and ws <= a["applied"] < we),
                            sum(1 for iv in data["interviews"] if ws <= iv[3] < we)))
    order = [f[0] for f in bt.FLAGS]
    flagged = sorted((a for a in apps if a["flag"]),
                     key=lambda a: (order.index(a["flag"]), a["nxt"] or a["applied"], a["row"]))
    m["action_list"] = [(a["flag"], a["co"]) for a in flagged]
    m["fu_counts"] = [sum(a["flag"] == f for a in apps) for f in order]
    act = sorted((a for a in apps if a["active"]), key=lambda a: (10 - a["rank"], a["applied"], a["row"]))
    m["active_list"] = [f"{a['co']} — {a['title']}" for a in act]
    m["contact_list"] = [c[0] for i, c in sorted(enumerate(data["contacts"]), key=lambda t: (t[1][3] or dt.date.max, t[0]))
                         if c[3] and (c[3] - today).days <= ds]
    m["interview_list"] = [iv[0] for i, iv in sorted(enumerate(data["interviews"]), key=lambda t: (t[1][3], t[0]))
                           if 0 <= (iv[3] - today).days <= iw]
    m["companies"] = [(sum(1 for a in sub if a["co"] == co), sum(1 for a in apps if a["co"] == co and a["active"]),
                       sum(1 for c in data["contacts"] if c[1] == co)) for co, _ in data["companies"]]
    m["apps"] = apps
    return m


# ----------------------------------------------------------------------------- assertions
def check(label, actual, expected, tol=1e-6):
    CHECKED[0] += 1
    ok =(abs(actual - expected) <= tol) if isinstance(expected, (int, float)) and isinstance(actual, float) \
        else actual == expected
    if not ok:
        FAILS.append(f"{label}: expected {expected!r}, got {actual!r}")
    return ok


def col_values(x, sheet, col, top, n):
    vals = x.read(sheet, f"{col}{top}:{col}{top + n - 1}")
    return [v[0] if isinstance(v, list) else v for v in vals]


def nonblank(vals):
    return [v for v in vals if v not in ("", None)]


def verify_sample(path, today):
    refs, data = bt.build(path, sample=True, today=today)
    m = model(data, today)
    x = Excel(path)
    try:
        dash = "Job Search Dashboard"
        for key in ["total", "week", "month", "active", "ints", "finals", "offers", "accepted", "app_int", "int_final",
                    "final_offer", "offer_rate", "avg_days", "missing_date", "missing_role", "dup_ids"]:
            check(f"dashboard {key}", x.read(*refs[f"dash.{key}"]), m[key])
        _, top, n = refs["dash.pipeline"]
        check("dashboard pipeline", col_values(x, dash, "D", top, n), [float(v) for v in m["pipeline"]])
        for key, col_key in (("role", "dash.role"), ("source", "dash.source"), ("match", "dash.match")):
            _, top, n, col = refs[col_key]
            check(f"dashboard by {key}", col_values(x, dash, col, top, n), [float(v) for v in m[key]])
        for key, best in (("an.cv", "cv_best"), ("an.source", "source_best"), ("an.role", "role_best"),
                          ("an.match", "match_best")):
            got = x.read(*refs[f"dash.best.{key}"])
            check(f"dashboard best {key}", got if not m[best].startswith("Not enough") else got[:19], m[best])
        check("next id", x.read("Applications", "B3"), float(m["next_id"]))

        # Applications auto columns
        n_apps = len(m["apps"])
        rows = x.read("Applications", f"AC{bt.FIRST}:AF{bt.FIRST + n_apps - 1}")
        for a, (flag, band, furthest, days) in zip(m["apps"], rows):
            check(f"row {a['row']} flag", flag, a["flag"])
            check(f"row {a['row']} match category", band, a["band"])
            check(f"row {a['row']} furthest stage", furthest, a["furthest"] if a["co"] else "")
            check(f"row {a['row']} days to response", days, float(a["days"]) if a["days"] is not None else "")

        # Follow-Ups
        for key, expected in zip(["overdue", "today", "noresp", "soon"], m["fu_counts"]):
            check(f"follow-ups {key}", x.read(*refs[f"fu.{key}"]), float(expected))
        check("follow-ups contacts", x.read(*refs["fu.contacts"]), float(len(m["contact_list"])))
        check("follow-ups interviews", x.read(*refs["fu.interviews"]), float(len(m["interview_list"])))
        _, top, n = refs["fu.app_list"]
        got = x.read("Follow-Ups", f"B{top}:C{top + n - 1}")
        check("follow-ups action list", [tuple(r) for r in got if r[0]], m["action_list"])
        _, top, n = refs["fu.ct_list"]
        check("follow-ups contact list", nonblank(col_values(x, "Follow-Ups", "B", top, n)), m["contact_list"])
        _, top, n = refs["fu.iv_list"]
        check("follow-ups interview list", nonblank(col_values(x, "Follow-Ups", "C", top, n)), m["interview_list"])

        # Analytics
        for key, mk in (("an.total", "total"), ("an.ints", "ints"), ("an.rate", "app_int")):
            check(f"analytics {key}", x.read(*refs[key]), m[mk])
        for key, mk in (("an.cv", "cv"), ("an.role", "an_role"), ("an.source", "an_source"), ("an.match", "an_match")):
            _, top, n = refs[key]
            got = x.read("Analytics", f"C{top}:F{top + n - 1}")
            check(f"analytics {key} table", [(r[0], r[1], r[3]) for r in got],
                  [tuple(float(v) for v in t) for t in m[mk]])
        for key, mk in (("an.cv", "cv_best"), ("an.source", "source_best")):
            check(f"analytics {key} best", x.read(*refs[f"{key}.best"]), m[mk])
        _, top, n = refs["an.skills"]
        got = x.read("Analytics", f"C{top}:D{top + n - 1}")
        check("analytics skill tags", [tuple(r) for r in got], [tuple(float(v) for v in t) for t in m["skills"]])
        _, top, n = refs["an.weekly"]
        got = x.read("Analytics", f"C{top}:E{top + n - 1}")
        check("analytics weekly", [(r[0], r[1], r[2]) for r in got], [(w, float(a), float(i)) for w, a, i in m["weekly"]])
        _, top, n = refs["an.active"]
        check("analytics active list", nonblank(col_values(x, "Analytics", "B", top, n)), m["active_list"])

        # Companies auto columns
        got = x.read("Companies", f"M{bt.FIRST}:O{bt.FIRST + len(m['companies']) - 1}")
        check("companies counts", [tuple(r) for r in got], [tuple(float(v) for v in t) for t in m["companies"]])

        errors = {s: x.error_count(s) for s in SHEETS}
        for s, n in errors.items():
            check(f"formula errors on {s}", n, 0.0)
        print(f"sample workbook: {sum(errors.values()):.0f} formula errors across {len(SHEETS)} sheets")

        # thresholds are configurable: change them on Settings and the flags must follow
        x.write("Settings", "E5", 11)
        x.write("Settings", "E6", 1)
        m2 = model(data, today, fu=11, ds=1)
        rows = x.read("Applications", f"AC{bt.FIRST}:AC{bt.FIRST + n_apps - 1}")
        check("flags after Settings change", [r[0] for r in rows], [a["flag"] for a in m2["apps"]])
        _, top, n = refs["fu.app_list"]
        got = x.read("Follow-Ups", f"B{top}:C{top + n - 1}")
        check("action list after Settings change", [tuple(r) for r in got if r[0]], m2["action_list"])
        check("settings change had an effect", m2["action_list"] != m["action_list"], True)
    finally:
        x.close()


def verify_clean(path):
    refs, _ = bt.build(path)
    x = Excel(path)
    try:
        for key in ["total", "week", "month", "active", "ints", "finals", "offers", "accepted", "missing_date",
                    "missing_role", "dup_ids"]:
            check(f"clean dashboard {key}", x.read(*refs[f"dash.{key}"]), 0.0)
        check("clean banner", x.read("Job Search Dashboard", "B13")[:1], "✓")
        check("clean next id", x.read("Applications", "B3"), 1.0)
        errors = {s: x.error_count(s) for s in SHEETS}
        for s, n in errors.items():
            check(f"clean formula errors on {s}", n, 0.0)
        print(f"clean workbook: {sum(errors.values()):.0f} formula errors across {len(SHEETS)} sheets")
    finally:
        x.close()


SHEETS = ["Job Search Dashboard", "Follow-Ups", "Applications", "Interviews", "Companies", "Contacts", "Analytics",
          "Settings", "Guide"]

if __name__ == "__main__":
    today = dt.date.today()
    tmp = Path(tempfile.mkdtemp(prefix="tracker-test-"))
    verify_sample(tmp / "tracker_sample_test.xlsx", today)
    verify_clean(tmp / "tracker_clean_test.xlsx")
    if FAILS:
        print(f"\n{len(FAILS)} check(s) FAILED:")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print(f"All {CHECKED[0]} checks passed.")
