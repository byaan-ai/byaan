"""Seeded synthetic healthcare-ops data generator for the Byaan eval suite.

Creates a SQLite database and computes exact ground truths for every eval case.
Deterministic: same --seed always yields the same rows and the same
ground_truth.json (fixed base date, random.Random(seed), no datetime.now()).

Run from server/:
    uv run python -m evals.synthetic.generate_data --seed 42 \
        --db evals/synthetic/eval_data.db \
        --ground-truth evals/synthetic/ground_truth.json
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

BASE_DATE = datetime(2026, 1, 5)
DATA_END = datetime(2026, 7, 4, 23, 59, 59)

SITES = [
    {
        "id": 1,
        "name": "Cedar Valley Clinic",
        "slug": "cedar_valley",
        "timezone": "America/Chicago",
        "utc_offset_hours": -6,
        "go_live_date": "2026-01-10",
        "data_start": datetime(2026, 1, 12),
    },
    {
        "id": 2,
        "name": "Cedar Ridge Medical",
        "slug": "cedar_ridge",
        "timezone": "America/New_York",
        "utc_offset_hours": -5,
        "go_live_date": "2026-01-15",
        "data_start": datetime(2026, 1, 17),
    },
    {
        "id": 3,
        "name": "Harbor Point Health",
        "slug": "harbor_point",
        "timezone": "America/Los_Angeles",
        "utc_offset_hours": -8,
        # go-live is Feb 1 but data does not begin until Mar 15 (audit layer newer).
        "go_live_date": "2026-02-01",
        "data_start": datetime(2026, 3, 15),
    },
    {
        "id": 4,
        "name": "Summit Care Center",
        "slug": "summit",
        "timezone": "America/Denver",
        "utc_offset_hours": -7,
        "go_live_date": "2026-01-20",
        "data_start": datetime(2026, 1, 22),
    },
    {
        "id": 5,
        "name": "Lakeside Family Practice",
        "slug": "lakeside",
        "timezone": "America/Phoenix",
        "utc_offset_hours": -7,
        "go_live_date": "2026-01-25",
        "data_start": datetime(2026, 1, 27),
    },
]

QUEUES = [
    {
        "id": 1,
        "site_id": 1,
        "name": "General Intake",
        "open_hour": 8,
        "close_hour": 18,
        "created": datetime(2026, 1, 12),
    },
    {"id": 2, "site_id": 1, "name": "Scheduling", "open_hour": 9, "close_hour": 17, "created": datetime(2026, 1, 12)},
    {
        "id": 3,
        "site_id": 2,
        "name": "General Intake",
        "open_hour": 9,
        "close_hour": 17,
        "created": datetime(2026, 1, 17),
    },
    {"id": 4, "site_id": 2, "name": "Triage Line", "open_hour": 7, "close_hour": 19, "created": datetime(2026, 1, 17)},
    {
        "id": 5,
        "site_id": 3,
        "name": "General Intake",
        "open_hour": 9,
        "close_hour": 17,
        "created": datetime(2026, 3, 15),
    },
    {
        "id": 6,
        "site_id": 4,
        "name": "General Intake",
        "open_hour": 9,
        "close_hour": 17,
        "created": datetime(2026, 1, 22),
    },
    {
        "id": 7,
        "site_id": 5,
        "name": "General Intake",
        "open_hour": 9,
        "close_hour": 17,
        "created": datetime(2026, 1, 27),
    },
    # Created mid-window (Apr 1) to trip naive date math on "since created".
    {
        "id": 8,
        "site_id": 1,
        "name": "Weekend Overflow",
        "open_hour": 10,
        "close_hour": 16,
        "created": datetime(2026, 4, 1),
    },
]

SITE_QUEUES = {1: [1, 2, 8], 2: [3, 4], 3: [5], 4: [6], 5: [7]}
CALLS_PER_SITE = {1: 6000, 2: 5000, 3: 3500, 4: 3200, 5: 2300}
CALL_STATUSES = ["completed", "missed", "ignored", "transferred", "voicemail"]
CALL_STATUS_WEIGHTS = [55, 18, 12, 10, 5]
NOTE_STATUSES = ["completed", "draft", "in_progress", "failed"]
NOTE_STATUS_WEIGHTS = [60, 18, 14, 8]
JOURNEY_STATUSES = ["active", "paused", "completed"]
JOURNEY_STATUS_WEIGHTS = [50, 20, 30]
PROGRAM_STATUSES = ["active", "paused", "completed", "withdrawn"]
PROGRAM_STATUS_WEIGHTS = [55, 15, 20, 10]
PROGRAM_NAMES = ["Cardiac Rehab", "Diabetes Management", "Post-Op Recovery", "Weight Management", "Behavioral Health"]


def _rand_dt(rng: random.Random, start: datetime, end: datetime) -> datetime:
    span = (end - start).total_seconds()
    return start + timedelta(seconds=rng.uniform(0, span))


def _to_local(dt: datetime, offset_hours: int) -> datetime:
    return dt + timedelta(hours=offset_hours)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def generate(rng: random.Random) -> dict[str, list]:
    site_by_id = {s["id"]: s for s in SITES}
    queue_by_id = {q["id"]: q for q in QUEUES}

    calls: list[dict] = []
    call_id = 0
    for site_id, count in CALLS_PER_SITE.items():
        site = site_by_id[site_id]
        queue_ids = SITE_QUEUES[site_id]
        for _ in range(count):
            call_id += 1
            queue_id = rng.choice(queue_ids)
            queue = queue_by_id[queue_id]
            start = max(site["data_start"], queue["created"])
            created = _rand_dt(rng, start, DATA_END)
            duration = rng.randint(8, 900)
            status = rng.choices(CALL_STATUSES, weights=CALL_STATUS_WEIGHTS, k=1)[0]
            calls.append(
                {
                    "id": call_id,
                    "site_id": site_id,
                    "queue_id": queue_id,
                    "patient_id": None,
                    "created_at": created,
                    "duration_seconds": duration,
                    "status": status,
                }
            )

    patients: list[dict] = []
    patient_id = 0
    patient_site_weights = [6, 5, 3, 3, 2]
    for _ in range(3000):
        patient_id += 1
        site = rng.choices(SITES, weights=patient_site_weights, k=1)[0]
        created = _rand_dt(rng, site["data_start"], DATA_END)
        patients.append(
            {
                "id": patient_id,
                "site_id": site["id"],
                "created_at": created,
                "mrn": f"MRN{100000 + patient_id}",
            }
        )

    for c in calls:
        pool = [p for p in patients if p["site_id"] == c["site_id"]]
        c["patient_id"] = rng.choice(pool)["id"] if pool else None

    enrollments: list[dict] = []
    enroll_id = 0
    enrolled_patients = [p for p in patients if rng.random() < 0.9]
    for p in enrolled_patients:
        enroll_id += 1
        site = site_by_id[p["site_id"]]
        enrolled_at = _rand_dt(rng, max(site["data_start"], p["created_at"]), DATA_END)
        enrollments.append(
            {
                "id": enroll_id,
                "patient_id": p["id"],
                "site_id": p["site_id"],
                "program_name": rng.choice(PROGRAM_NAMES),
                "enrolled_at": enrolled_at,
            }
        )

    # patient_programs deliberately diverges from enrollments by ~4%.
    patient_programs: list[dict] = []
    pp_id = 0
    for e in enrollments:
        pp_id += 1
        patient_programs.append(
            {
                "id": pp_id,
                "patient_id": e["patient_id"],
                "site_id": e["site_id"],
                "program_name": e["program_name"],
                "status": rng.choices(PROGRAM_STATUSES, weights=PROGRAM_STATUS_WEIGHTS, k=1)[0],
            }
        )
    extra = round(len(enrollments) * 0.04)
    for _ in range(extra):
        pp_id += 1
        p = rng.choice(patients)
        patient_programs.append(
            {
                "id": pp_id,
                "patient_id": p["id"],
                "site_id": p["site_id"],
                "program_name": rng.choice(PROGRAM_NAMES),
                "status": rng.choices(PROGRAM_STATUSES, weights=PROGRAM_STATUS_WEIGHTS, k=1)[0],
            }
        )

    journeys: list[dict] = []
    journey_steps: list[dict] = []
    journey_id = 0
    step_id = 0
    journey_patients = [p for p in patients if rng.random() < 0.5]
    for p in journey_patients:
        journey_id += 1
        site = site_by_id[p["site_id"]]
        status = rng.choices(JOURNEY_STATUSES, weights=JOURNEY_STATUS_WEIGHTS, k=1)[0]
        started_at = _rand_dt(rng, max(site["data_start"], p["created_at"]), DATA_END)
        journeys.append(
            {
                "id": journey_id,
                "site_id": p["site_id"],
                "patient_id": p["id"],
                "name": rng.choice(["Onboarding", "Pre-Op", "Post-Op Follow-Up", "Wellness Check"]),
                "status": status,
                "started_at": started_at,
            }
        )
        n_steps = rng.randint(3, 6)
        for order in range(1, n_steps + 1):
            step_id += 1
            completed = 1 if (status == "completed" or rng.random() < 0.5) else 0
            journey_steps.append(
                {
                    "id": step_id,
                    "journey_id": journey_id,
                    "step_order": order,
                    "name": f"Step {order}",
                    "completed": completed,
                }
            )

    sms_messages: list[dict] = []
    sms_id = 0
    for _ in range(15000):
        sms_id += 1
        p = rng.choice(patients)
        site = site_by_id[p["site_id"]]
        sent_at = _rand_dt(rng, max(site["data_start"], p["created_at"]), DATA_END)
        direction = rng.choices(["outbound", "inbound"], weights=[70, 30], k=1)[0]
        sms_messages.append(
            {
                "id": sms_id,
                "site_id": p["site_id"],
                "patient_id": p["id"],
                "direction": direction,
                "body": "Appointment reminder" if direction == "outbound" else "Reply text",
                "sent_at": sent_at,
            }
        )

    surveys: list[dict] = []
    survey_id = 0
    survey_patients = [p for p in patients if rng.random() < 0.65]
    for p in survey_patients:
        survey_id += 1
        site = site_by_id[p["site_id"]]
        submitted_at = _rand_dt(rng, max(site["data_start"], p["created_at"]), DATA_END)
        score = rng.randint(1, 10)
        surveys.append(
            {
                "id": survey_id,
                "site_id": p["site_id"],
                "patient_id": p["id"],
                "score": score,
                "submitted_at": submitted_at,
            }
        )

    notes: list[dict] = []
    note_id = 0
    for _ in range(4000):
        note_id += 1
        p = rng.choice(patients)
        site = site_by_id[p["site_id"]]
        created = _rand_dt(rng, max(site["data_start"], p["created_at"]), DATA_END)
        status = rng.choices(NOTE_STATUSES, weights=NOTE_STATUS_WEIGHTS, k=1)[0]
        notes.append(
            {
                "id": note_id,
                "site_id": p["site_id"],
                "patient_id": p["id"],
                "note_status": status,
                # Restricted free text: never quantify categories from this column.
                "free_text": "Patient reported symptoms during the visit.",
                "created_at": created,
            }
        )

    return {
        "sites": SITES,
        "queues": QUEUES,
        "calls": calls,
        "patients": patients,
        "enrollments": enrollments,
        "patient_programs": patient_programs,
        "journeys": journeys,
        "journey_steps": journey_steps,
        "sms_messages": sms_messages,
        "surveys": surveys,
        "notes": notes,
    }


def compute_ground_truth(data: dict[str, list]) -> dict:
    site_by_id = {s["id"]: s for s in SITES}
    queue_by_id = {q["id"]: q for q in QUEUES}
    calls = data["calls"]
    gt: dict = {}

    gt["total_calls"] = len(calls)
    gt["total_patients"] = len(data["patients"])
    gt["total_enrollments"] = len(data["enrollments"])
    gt["total_patient_programs"] = len(data["patient_programs"])
    gt["total_sms"] = len(data["sms_messages"])
    gt["total_surveys"] = len(data["surveys"])
    gt["total_journeys"] = len(data["journeys"])
    gt["total_notes"] = len(data["notes"])

    for s in SITES:
        slug = s["slug"]
        sc = [c for c in calls if c["site_id"] == s["id"]]
        gt[f"calls_site_{slug}"] = len(sc)
        gt[f"patients_site_{slug}"] = len([p for p in data["patients"] if p["site_id"] == s["id"]])
        gt[f"enrollments_site_{slug}"] = len([e for e in data["enrollments"] if e["site_id"] == s["id"]])
        gt[f"patient_programs_site_{slug}"] = len([p for p in data["patient_programs"] if p["site_id"] == s["id"]])
        gt[f"go_live_date_site_{slug}"] = s["go_live_date"]

    # June calls at Cedar Valley in site-local time (the correct window) vs naive UTC (the trap).
    cv = site_by_id[1]
    cv_calls = [c for c in calls if c["site_id"] == 1]
    jun_start, jul_start = datetime(2026, 6, 1), datetime(2026, 7, 1)
    gt["calls_june_local_site_cedar_valley"] = len(
        [c for c in cv_calls if jun_start <= _to_local(c["created_at"], cv["utc_offset_hours"]) < jul_start]
    )
    gt["calls_june_utc_site_cedar_valley"] = len([c for c in cv_calls if jun_start <= c["created_at"] < jul_start])
    may_start = datetime(2026, 5, 1)
    apr_start = datetime(2026, 4, 1)
    gt["calls_may_local_site_cedar_valley"] = len(
        [c for c in cv_calls if may_start <= _to_local(c["created_at"], cv["utc_offset_hours"]) < jun_start]
    )
    gt["calls_apr_local_site_cedar_valley"] = len(
        [c for c in cv_calls if apr_start <= _to_local(c["created_at"], cv["utc_offset_hours"]) < may_start]
    )
    for sid, slug in ((2, "cedar_ridge"), (3, "harbor_point")):
        s = site_by_id[sid]
        sc = [c for c in calls if c["site_id"] == sid]
        gt[f"calls_june_local_site_{slug}"] = len(
            [c for c in sc if jun_start <= _to_local(c["created_at"], s["utc_offset_hours"]) < jul_start]
        )

    # After-hours vs business-hours by each queue's own schedule (site-local clock).
    for qid in (1, 2):
        q = queue_by_id[qid]
        site = site_by_id[q["site_id"]]
        qc = [c for c in calls if c["queue_id"] == qid]
        business = 0
        for c in qc:
            local_hour = _to_local(c["created_at"], site["utc_offset_hours"]).hour
            if q["open_hour"] <= local_hour < q["close_hour"]:
                business += 1
        gt[f"business_hours_calls_queue_{qid}"] = business
        gt[f"after_hours_calls_queue_{qid}"] = len(qc) - business

    # After-hours across all Cedar Valley queues, each using its own schedule.
    cv_after = 0
    for c in cv_calls:
        q = queue_by_id[c["queue_id"]]
        local_hour = _to_local(c["created_at"], cv["utc_offset_hours"]).hour
        if not (q["open_hour"] <= local_hour < q["close_hour"]):
            cv_after += 1
    gt["after_hours_calls_site_cedar_valley"] = cv_after

    # Weekend Overflow queue created mid-window (Apr 1).
    gt["calls_queue_weekend_overflow"] = len([c for c in calls if c["queue_id"] == 8])

    # Harbor Point calls since go-live (all data lands after go-live).
    hp = site_by_id[3]
    go_live_dt = datetime.strptime(hp["go_live_date"], "%Y-%m-%d")
    gt["calls_since_golive_harbor_point"] = len(
        [c for c in calls if c["site_id"] == 3 and c["created_at"] >= go_live_dt]
    )

    scores = [sv["score"] for sv in data["surveys"]]
    gt["avg_survey_score_overall"] = round(sum(scores) / len(scores), 2)
    cv_scores = [sv["score"] for sv in data["surveys"] if sv["site_id"] == 1]
    gt["avg_survey_score_site_cedar_valley"] = round(sum(cv_scores) / len(cv_scores), 2)

    gt["journeys_active"] = len([j for j in data["journeys"] if j["status"] == "active"])
    gt["journeys_paused"] = len([j for j in data["journeys"] if j["status"] == "paused"])

    for st in NOTE_STATUSES:
        gt[f"notes_status_{st}"] = len([n for n in data["notes"] if n["note_status"] == st])
    # "Incomplete" scribe notes = draft/in_progress/failed (never a literal 'incomplete' status).
    gt["notes_incomplete"] = len([n for n in data["notes"] if n["note_status"] in ("draft", "in_progress", "failed")])

    gt["sms_outbound_total"] = len([m for m in data["sms_messages"] if m["direction"] == "outbound"])
    cv_out_june = [
        m
        for m in data["sms_messages"]
        if m["site_id"] == 1
        and m["direction"] == "outbound"
        and jun_start <= _to_local(m["sent_at"], cv["utc_offset_hours"]) < jul_start
    ]
    gt["sms_outbound_june_site_cedar_valley"] = len(cv_out_june)

    return gt


def _insert(conn: sqlite3.Connection, data: dict[str, list]) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS sites;
        DROP TABLE IF EXISTS call_queues;
        DROP TABLE IF EXISTS queue_schedules;
        DROP TABLE IF EXISTS calls;
        DROP TABLE IF EXISTS patients;
        DROP TABLE IF EXISTS enrollments;
        DROP TABLE IF EXISTS patient_programs;
        DROP TABLE IF EXISTS journeys;
        DROP TABLE IF EXISTS journey_steps;
        DROP TABLE IF EXISTS sms_messages;
        DROP TABLE IF EXISTS surveys;
        DROP TABLE IF EXISTS notes;

        CREATE TABLE sites (
            id INTEGER PRIMARY KEY, name TEXT, timezone TEXT,
            utc_offset_hours INTEGER, go_live_date TEXT
        );
        CREATE TABLE call_queues (
            id INTEGER PRIMARY KEY, site_id INTEGER, name TEXT,
            created_at TEXT
        );
        CREATE TABLE queue_schedules (
            id INTEGER PRIMARY KEY, queue_id INTEGER, day_of_week INTEGER,
            open_hour INTEGER, close_hour INTEGER
        );
        CREATE TABLE calls (
            id INTEGER PRIMARY KEY, site_id INTEGER, queue_id INTEGER,
            patient_id INTEGER, created_at TEXT, duration_seconds INTEGER, status TEXT
        );
        CREATE TABLE patients (
            id INTEGER PRIMARY KEY, site_id INTEGER, created_at TEXT, mrn TEXT
        );
        CREATE TABLE enrollments (
            id INTEGER PRIMARY KEY, patient_id INTEGER, site_id INTEGER,
            program_name TEXT, enrolled_at TEXT
        );
        CREATE TABLE patient_programs (
            id INTEGER PRIMARY KEY, patient_id INTEGER, site_id INTEGER,
            program_name TEXT, status TEXT
        );
        CREATE TABLE journeys (
            id INTEGER PRIMARY KEY, site_id INTEGER, patient_id INTEGER,
            name TEXT, status TEXT, started_at TEXT
        );
        CREATE TABLE journey_steps (
            id INTEGER PRIMARY KEY, journey_id INTEGER, step_order INTEGER,
            name TEXT, completed INTEGER
        );
        CREATE TABLE sms_messages (
            id INTEGER PRIMARY KEY, site_id INTEGER, patient_id INTEGER,
            direction TEXT, body TEXT, sent_at TEXT
        );
        CREATE TABLE surveys (
            id INTEGER PRIMARY KEY, site_id INTEGER, patient_id INTEGER,
            score INTEGER, submitted_at TEXT
        );
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY, site_id INTEGER, patient_id INTEGER,
            note_status TEXT, free_text TEXT, created_at TEXT
        );
        """
    )

    cur.executemany(
        "INSERT INTO sites VALUES (?,?,?,?,?)",
        [(s["id"], s["name"], s["timezone"], s["utc_offset_hours"], s["go_live_date"]) for s in data["sites"]],
    )
    cur.executemany(
        "INSERT INTO call_queues VALUES (?,?,?,?)",
        [(q["id"], q["site_id"], q["name"], _fmt(q["created"])) for q in data["queues"]],
    )
    sched_rows = []
    sched_id = 0
    for q in data["queues"]:
        for dow in range(7):
            sched_id += 1
            sched_rows.append((sched_id, q["id"], dow, q["open_hour"], q["close_hour"]))
    cur.executemany("INSERT INTO queue_schedules VALUES (?,?,?,?,?)", sched_rows)
    cur.executemany(
        "INSERT INTO calls VALUES (?,?,?,?,?,?,?)",
        [
            (
                c["id"],
                c["site_id"],
                c["queue_id"],
                c["patient_id"],
                _fmt(c["created_at"]),
                c["duration_seconds"],
                c["status"],
            )
            for c in data["calls"]
        ],
    )
    cur.executemany(
        "INSERT INTO patients VALUES (?,?,?,?)",
        [(p["id"], p["site_id"], _fmt(p["created_at"]), p["mrn"]) for p in data["patients"]],
    )
    cur.executemany(
        "INSERT INTO enrollments VALUES (?,?,?,?,?)",
        [
            (e["id"], e["patient_id"], e["site_id"], e["program_name"], _fmt(e["enrolled_at"]))
            for e in data["enrollments"]
        ],
    )
    cur.executemany(
        "INSERT INTO patient_programs VALUES (?,?,?,?,?)",
        [(p["id"], p["patient_id"], p["site_id"], p["program_name"], p["status"]) for p in data["patient_programs"]],
    )
    cur.executemany(
        "INSERT INTO journeys VALUES (?,?,?,?,?,?)",
        [
            (j["id"], j["site_id"], j["patient_id"], j["name"], j["status"], _fmt(j["started_at"]))
            for j in data["journeys"]
        ],
    )
    cur.executemany(
        "INSERT INTO journey_steps VALUES (?,?,?,?,?)",
        [(s["id"], s["journey_id"], s["step_order"], s["name"], s["completed"]) for s in data["journey_steps"]],
    )
    cur.executemany(
        "INSERT INTO sms_messages VALUES (?,?,?,?,?,?)",
        [
            (m["id"], m["site_id"], m["patient_id"], m["direction"], m["body"], _fmt(m["sent_at"]))
            for m in data["sms_messages"]
        ],
    )
    cur.executemany(
        "INSERT INTO surveys VALUES (?,?,?,?,?)",
        [(s["id"], s["site_id"], s["patient_id"], s["score"], _fmt(s["submitted_at"])) for s in data["surveys"]],
    )
    cur.executemany(
        "INSERT INTO notes VALUES (?,?,?,?,?,?)",
        [
            (n["id"], n["site_id"], n["patient_id"], n["note_status"], n["free_text"], _fmt(n["created_at"]))
            for n in data["notes"]
        ],
    )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic Byaan eval data.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--db", type=str, default="evals/synthetic/eval_data.db")
    parser.add_argument("--ground-truth", type=str, default="evals/synthetic/ground_truth.json")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    data = generate(rng)
    gt = compute_ground_truth(data)

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        _insert(conn, data)
    finally:
        conn.close()

    gt_path = Path(args.ground_truth)
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    gt_path.write_text(json.dumps(gt, indent=2, sort_keys=True) + "\n")

    print(f"Wrote {db_path} and {gt_path}")
    print(f"  {gt['total_calls']} calls, {gt['total_patients']} patients, {gt['total_enrollments']} enrollments")


if __name__ == "__main__":
    main()
