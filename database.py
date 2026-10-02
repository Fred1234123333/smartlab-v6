"""
database.py
SQLite setup and all database operations for SmartLab (Flask version).

Tables:
  users          - admin accounts (hashed passwords)
  computers      - one row per registered computer
  reports        - problem reports filed against a computer
  settings       - key/value store for the Settings page (added for the
                   Settings feature; does not touch any existing table)
  laboratories   - optional reference info (name/location/description) for
                   labs, shown in Settings > Laboratory. Kept separate from
                   computers.lab (free text) so existing computer records
                   are never affected.
  activity_log   - login/logout/failed-login history for Settings > Security.
                   Never stores passwords.

Status model (intentionally simplified from the earlier desktop version,
per the project brief): Working / Under Repair / Offline.
"""

import sqlite3
import os
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "smartlab.db")

STATUS_OPTIONS = ["Working", "Under Repair", "Offline"]

ISSUE_TYPES = [
    "Monitor",
    "Keyboard",
    "Mouse",
    "System Unit",
    "Internet",
    "Software",
    "Power",
    "Other",
]

PRIORITY_OPTIONS = ["Critical", "High", "Medium", "Low"]
REPORT_STATUS_OPTIONS = ["Open", "Investigating", "Under Repair", "Resolved"]
MAINTENANCE_RESULT_OPTIONS = ["Resolved", "Partially Resolved", "Unresolved", "Pending Parts"]

QR_SIZE_OPTIONS = {"Small": 6, "Medium": 8, "Large": 12}  # label -> qrcode box_size
ACCENT_COLORS = ["Sky Green", "Bright Green", "Strong Green", "Deep Green"]
SESSION_TIMEOUT_OPTIONS = [15, 30, 60, 120]

# Every setting the Settings page can read/write, with a safe default.
# Stored as plain strings in the settings table and parsed where needed.
DEFAULT_SETTINGS = {
    "system_name": "SmartLab",
    "system_description": "QR-Based Computer Laboratory Monitoring System",
    "admin_email": "",
    "default_lab": "",
    "allow_anonymous_reports": "on",

    "theme": "light",
    "accent_color": "Strong Green",
    "compact_mode": "off",
    "animations": "on",

    "qr_size": "Small",

    "session_timeout": "30",
    "auto_logout": "on",

    "notif_new_report": "on",
    "notif_resolved_report": "on",
    "notif_system_alerts": "on",
    "notif_sound": "off",

    "about_developers": "",
    "about_course": "",
    "about_section": "",
    "about_school": "",
}


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database():
    """Create tables if they don't exist yet. Never drops or overwrites existing data."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS computers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computer_id TEXT UNIQUE NOT NULL,
            lab TEXT NOT NULL,
            number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Working',
            qr_path TEXT,
            date_added TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computer_id TEXT NOT NULL,
            issue_type TEXT NOT NULL,
            description TEXT,
            reporter TEXT,
            date_reported TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Open',
            date_resolved TEXT,
            FOREIGN KEY (computer_id) REFERENCES computers(computer_id)
        )
    """)

    # New tables for the Settings feature - additive only, nothing above is touched.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS laboratories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            location TEXT,
            description TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            username TEXT,
            timestamp TEXT NOT NULL,
            detail TEXT
        )
    """)

    # Additive migration: older installs may have an activity_log table from
    # before "category" and "computer_id" existed. Add them safely without
    # touching any existing rows or other tables.
    existing_cols = [row["name"] for row in cur.execute("PRAGMA table_info(activity_log)").fetchall()]
    if "category" not in existing_cols:
        cur.execute("ALTER TABLE activity_log ADD COLUMN category TEXT")
    if "computer_id" not in existing_cols:
        cur.execute("ALTER TABLE activity_log ADD COLUMN computer_id TEXT")

    # ---- Additive migration: optional computer specification fields ----
    # All nullable, so every existing computer record stays exactly as it
    # was - these columns simply read as empty until an admin fills them in.
    computer_cols = [row["name"] for row in cur.execute("PRAGMA table_info(computers)").fetchall()]
    for col in ["cpu", "ram", "storage", "os", "monitor", "keyboard", "mouse", "internet", "specs_notes"]:
        if col not in computer_cols:
            cur.execute(f"ALTER TABLE computers ADD COLUMN {col} TEXT")

    # ---- Additive migration: report priority ----
    # Existing reports get 'Medium' so nothing is left blank/invalid; new
    # reports default to 'Medium' too unless an admin changes it.
    report_cols = [row["name"] for row in cur.execute("PRAGMA table_info(reports)").fetchall()]
    if "priority" not in report_cols:
        cur.execute("ALTER TABLE reports ADD COLUMN priority TEXT NOT NULL DEFAULT 'Medium'")

    # ---- New table: computer_status_history ----
    # Every status change - manual or automatic - is recorded here and never
    # overwritten, per the "never silently overwrite important history" rule.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS computer_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computer_id TEXT NOT NULL,
            old_status TEXT,
            new_status TEXT NOT NULL,
            reason TEXT,
            changed_by TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (computer_id) REFERENCES computers(computer_id)
        )
    """)

    # ---- New table: maintenance ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS maintenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computer_id TEXT NOT NULL,
            report_id INTEGER,
            technician TEXT,
            problem TEXT,
            diagnosis TEXT,
            action_taken TEXT,
            parts_replaced TEXT,
            start_date TEXT NOT NULL,
            completion_date TEXT,
            result TEXT,
            remarks TEXT,
            FOREIGN KEY (computer_id) REFERENCES computers(computer_id),
            FOREIGN KEY (report_id) REFERENCES reports(id)
        )
    """)

    # ---- Additive migration: laboratories become real physical-location records ----
    # A laboratory is the actual room the computers sit in, not just a label.
    # All new columns are nullable so existing lab reference rows (name/location/
    # description only) keep working exactly as before, with the new fields blank
    # until an admin fills them in.
    lab_cols = [row["name"] for row in cur.execute("PRAGMA table_info(laboratories)").fetchall()]
    for col, coltype in [
        ("code", "TEXT"), ("building", "TEXT"), ("floor", "TEXT"), ("room", "TEXT"),
        ("status", "TEXT NOT NULL DEFAULT 'Active'"), ("date_created", "TEXT"),
    ]:
        if col not in lab_cols:
            cur.execute(f"ALTER TABLE laboratories ADD COLUMN {col} {coltype}")

    # ---- Additive migration: computers actually belong to a laboratory record ----
    # computers.lab (free text) is kept exactly as-is for backward compatibility -
    # nothing that already reads it breaks. laboratory_id is the new real link.
    computer_cols2 = [row["name"] for row in cur.execute("PRAGMA table_info(computers)").fetchall()]
    if "laboratory_id" not in computer_cols2:
        cur.execute("ALTER TABLE computers ADD COLUMN laboratory_id INTEGER REFERENCES laboratories(id)")

    # One-time backfill: every distinct lab name already used by a computer gets a
    # real laboratory row (auto-created if one doesn't already exist by that name),
    # and every computer is linked to it. This runs every startup but is a no-op
    # once everything is already linked, so it is always safe to re-run.
    distinct_labs = [r["lab"] for r in cur.execute(
        "SELECT DISTINCT lab FROM computers WHERE laboratory_id IS NULL"
    ).fetchall()]
    for lab_name in distinct_labs:
        existing = cur.execute("SELECT id FROM laboratories WHERE name=?", (lab_name,)).fetchone()
        if existing:
            lab_id = existing["id"]
        else:
            cur.execute(
                "INSERT INTO laboratories (name, date_created) VALUES (?, ?)",
                (lab_name, datetime.now().strftime("%Y-%m-%d %H:%M")),
            )
            lab_id = cur.lastrowid
        cur.execute("UPDATE computers SET laboratory_id=? WHERE lab=? AND laboratory_id IS NULL", (lab_id, lab_name))

    # ---- Indexes on frequently filtered/searched columns ----
    # Kept deliberately small - one index per column that's actually used in
    # a WHERE/ORDER BY, not a blanket index on every field.
    cur.execute("CREATE INDEX IF NOT EXISTS idx_computers_status ON computers(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_computers_lab ON computers(lab)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reports_computer_id ON reports(computer_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reports_priority ON reports(priority)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(date_reported)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_status_history_computer_id ON computer_status_history(computer_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_computer_id ON maintenance(computer_id)")

    # Seed default settings values the first time only - never overwrites a
    # value the administrator has already changed.
    for key, value in DEFAULT_SETTINGS.items():
        cur.execute(
            "INSERT OR IGNORE INTO settings (setting_key, setting_value) VALUES (?, ?)",
            (key, value),
        )

    # Seed a default admin the first time only (never overwrites an existing account)
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        from werkzeug.security import generate_password_hash
        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("admin", generate_password_hash("admin123")),
        )

    conn.commit()
    conn.close()


# ---------- USERS ----------

def get_user(username):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return row


def update_password(username, new_hash):
    conn = get_connection()
    conn.execute("UPDATE users SET password_hash=? WHERE username=?", (new_hash, username))
    conn.commit()
    conn.close()


# ---------- COMPUTERS ----------

def add_computer(computer_id, lab, number, qr_path, laboratory_id=None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO computers (computer_id, lab, number, status, qr_path, date_added, laboratory_id) "
        "VALUES (?, ?, ?, 'Working', ?, ?, ?)",
        (computer_id, lab, number, qr_path, datetime.now().strftime("%Y-%m-%d %H:%M"), laboratory_id),
    )
    conn.commit()
    conn.close()
    log_activity(f"{computer_id} added to {lab}", category="computer_added", computer_id=computer_id)


def computer_exists(computer_id):
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM computers WHERE computer_id=?", (computer_id,)).fetchone()
    conn.close()
    return row is not None


def get_all_computers(status_filter=None, search=None):
    conn = get_connection()
    query = "SELECT * FROM computers"
    conditions = []
    params = []
    if status_filter and status_filter != "All":
        conditions.append("status = ?")
        params.append(status_filter)
    if search:
        conditions.append("(computer_id LIKE ? OR CAST(number AS TEXT) LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY lab, number"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def get_all_computers_with_lab():
    """Every computer with its laboratory's id/code/location joined in, for
    pages that group computers by physical laboratory. Computers with no
    laboratory_id (e.g. after their laboratory was deleted) get NULL lab_* -
    the template shows those under an "Unassigned" section rather than
    silently dropping them."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT computers.*, "
        "laboratories.code AS lab_code, laboratories.building AS lab_building, "
        "laboratories.floor AS lab_floor, laboratories.room AS lab_room, "
        "laboratories.location AS lab_location "
        "FROM computers LEFT JOIN laboratories ON computers.laboratory_id = laboratories.id "
        "ORDER BY laboratories.name, computers.number"
    ).fetchall()
    conn.close()
    return rows


def update_computer(old_computer_id, new_computer_id, laboratory_id, number, new_qr_path=None):
    """Edits a computer's ID, laboratory, and workstation number. If the ID
    actually changes, every report, status-history entry, and maintenance
    record tied to the old ID is moved to the new one too - otherwise a typo
    fix would silently orphan that computer's whole history. Pass new_qr_path
    only when the ID changed and a fresh QR image was generated for it.

    reports/computer_status_history/maintenance all declare a real FOREIGN KEY
    on computer_id, so renaming it has no valid statement order that satisfies
    the constraint at every intermediate step - foreign_keys is turned off for
    just this one connection/transaction, exactly like a real foreign key
    rename is normally handled, then re-enabled before the connection closes.
    """
    conn = get_connection()
    id_changed = new_computer_id != old_computer_id
    if id_changed:
        conn.execute("PRAGMA foreign_keys = OFF")
    lab_row = conn.execute("SELECT name FROM laboratories WHERE id=?", (laboratory_id,)).fetchone()
    lab_name = lab_row["name"] if lab_row else None
    if new_qr_path:
        conn.execute(
            "UPDATE computers SET computer_id=?, lab=?, laboratory_id=?, number=?, qr_path=? WHERE computer_id=?",
            (new_computer_id, lab_name, laboratory_id, number, new_qr_path, old_computer_id),
        )
    else:
        conn.execute(
            "UPDATE computers SET computer_id=?, lab=?, laboratory_id=?, number=? WHERE computer_id=?",
            (new_computer_id, lab_name, laboratory_id, number, old_computer_id),
        )
    if id_changed:
        conn.execute("UPDATE reports SET computer_id=? WHERE computer_id=?", (new_computer_id, old_computer_id))
        conn.execute("UPDATE computer_status_history SET computer_id=? WHERE computer_id=?", (new_computer_id, old_computer_id))
        conn.execute("UPDATE maintenance SET computer_id=? WHERE computer_id=?", (new_computer_id, old_computer_id))
    conn.commit()
    if id_changed:
        conn.execute("PRAGMA foreign_keys = ON")
    conn.close()


def get_computer(computer_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM computers WHERE computer_id=?", (computer_id,)).fetchone()
    conn.close()
    return row


def get_computer_with_lab(computer_id):
    """Same as get_computer(), plus the full physical location from the linked
    laboratory record (building/floor/room/code) when one exists."""
    conn = get_connection()
    row = conn.execute(
        "SELECT computers.*, "
        "laboratories.code AS lab_code, laboratories.building AS lab_building, "
        "laboratories.floor AS lab_floor, laboratories.room AS lab_room, "
        "laboratories.location AS lab_location "
        "FROM computers LEFT JOIN laboratories ON computers.laboratory_id = laboratories.id "
        "WHERE computers.computer_id=?",
        (computer_id,),
    ).fetchone()
    conn.close()
    return row


def update_status(computer_id, new_status, reason=None, changed_by=None):
    conn = get_connection()
    old_row = conn.execute("SELECT status FROM computers WHERE computer_id=?", (computer_id,)).fetchone()
    old_status = old_row["status"] if old_row else None
    conn.execute("UPDATE computers SET status=? WHERE computer_id=?", (new_status, computer_id))
    if old_status != new_status:
        conn.execute(
            "INSERT INTO computer_status_history "
            "(computer_id, old_status, new_status, reason, changed_by, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (computer_id, old_status, new_status, reason, changed_by,
             datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
    conn.commit()
    conn.close()


def get_status_history(computer_id, limit=50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM computer_status_history WHERE computer_id=? "
        "ORDER BY id DESC LIMIT ?",
        (computer_id, limit),
    ).fetchall()
    conn.close()
    return rows


def delete_computer(computer_id):
    conn = get_connection()
    conn.execute("DELETE FROM reports WHERE computer_id=?", (computer_id,))
    conn.execute("DELETE FROM computers WHERE computer_id=?", (computer_id,))
    conn.commit()
    conn.close()


def get_open_report_count(computer_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE computer_id=? AND status!='Resolved'", (computer_id,)
    ).fetchone()
    conn.close()
    return row[0]


SPEC_FIELDS = ["cpu", "ram", "storage", "os", "monitor", "keyboard", "mouse", "internet", "specs_notes"]


def update_computer_specs(computer_id, specs: dict):
    """specs is a dict with any subset of SPEC_FIELDS - all optional."""
    conn = get_connection()
    fields = [f for f in SPEC_FIELDS if f in specs]
    if not fields:
        conn.close()
        return
    set_clause = ", ".join(f"{f}=?" for f in fields)
    values = [specs[f] for f in fields] + [computer_id]
    conn.execute(f"UPDATE computers SET {set_clause} WHERE computer_id=?", values)
    conn.commit()
    conn.close()


def get_labs():
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT lab FROM computers ORDER BY lab").fetchall()
    conn.close()
    return [r[0] for r in rows]


# ---------- REPORTS ----------

def create_report(computer_id, issue_type, description, reporter, priority="Medium"):
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO reports (computer_id, issue_type, description, reporter, date_reported, status, priority) "
        "VALUES (?, ?, ?, ?, ?, 'Open', ?)",
        (computer_id, issue_type, description, reporter, datetime.now().strftime("%Y-%m-%d %H:%M"), priority),
    )
    report_id = cur.lastrowid
    conn.commit()
    conn.close()
    # A new report flags the computer as needing attention
    update_status(computer_id, "Under Repair", reason=f"New report: {issue_type}", changed_by=reporter or "Public report")
    log_activity(f"{computer_id} reported {issue_type}", category="report_new", computer_id=computer_id)
    return report_id


def get_report(report_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    conn.close()
    return row


REPORTS_PER_PAGE = 20


def get_reports(status=None, page=1, per_page=None, lab_id=None):
    conn = get_connection()
    base = (
        "SELECT reports.*, computers.lab, computers.number, computers.laboratory_id, "
        "laboratories.location AS lab_location, laboratories.code AS lab_code "
        "FROM reports "
        "JOIN computers ON reports.computer_id = computers.computer_id "
        "LEFT JOIN laboratories ON computers.laboratory_id = laboratories.id "
    )
    conditions = []
    params = []
    if status:
        conditions.append("reports.status=?")
        params.append(status)
    if lab_id:
        conditions.append("computers.laboratory_id=?")
        params.append(lab_id)
    if conditions:
        base += "WHERE " + " AND ".join(conditions) + " "
    base += "ORDER BY laboratories.name, reports.date_reported DESC"
    if per_page:
        base += " LIMIT ? OFFSET ?"
        params += [per_page, (page - 1) * per_page]
    rows = conn.execute(base, params).fetchall()
    conn.close()
    return rows


def count_reports(status=None, lab_id=None):
    conn = get_connection()
    query = "SELECT COUNT(*) FROM reports JOIN computers ON reports.computer_id = computers.computer_id"
    conditions = []
    params = []
    if status:
        conditions.append("reports.status=?")
        params.append(status)
    if lab_id:
        conditions.append("computers.laboratory_id=?")
        params.append(lab_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    n = conn.execute(query, params).fetchone()[0]
    conn.close()
    return n


def get_open_reports(page=1, per_page=None, lab_id=None):
    """All reports not yet Resolved - i.e. Open, Investigating, or Under Repair."""
    conn = get_connection()
    query = (
        "SELECT reports.*, computers.lab, computers.number, computers.laboratory_id, "
        "laboratories.location AS lab_location, laboratories.code AS lab_code "
        "FROM reports "
        "JOIN computers ON reports.computer_id = computers.computer_id "
        "LEFT JOIN laboratories ON computers.laboratory_id = laboratories.id "
        "WHERE reports.status!='Resolved' "
    )
    params = []
    if lab_id:
        query += "AND computers.laboratory_id=? "
        params.append(lab_id)
    query += (
        "ORDER BY laboratories.name, CASE reports.priority "
        "  WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, "
        "reports.date_reported DESC"
    )
    if per_page:
        query += " LIMIT ? OFFSET ?"
        params += [per_page, (page - 1) * per_page]
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def count_open_reports(lab_id=None):
    conn = get_connection()
    if lab_id:
        n = conn.execute(
            "SELECT COUNT(*) FROM reports JOIN computers ON reports.computer_id = computers.computer_id "
            "WHERE reports.status!='Resolved' AND computers.laboratory_id=?", (lab_id,)
        ).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM reports WHERE status!='Resolved'").fetchone()[0]
    conn.close()
    return n


def get_reports_for_computer(computer_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reports WHERE computer_id=? ORDER BY date_reported DESC", (computer_id,)
    ).fetchall()
    conn.close()
    return rows


def update_report_status(report_id, new_status, changed_by=None):
    """Moves a report through Open -> Investigating -> Under Repair -> Resolved.
    Resolving still restores the computer's status when it has no other open reports,
    exactly as before - this just generalizes resolve_report() to the full workflow."""
    conn = get_connection()
    report = conn.execute("SELECT computer_id, status FROM reports WHERE id=?", (report_id,)).fetchone()
    if not report:
        conn.close()
        return False
    if new_status == "Resolved":
        conn.execute(
            "UPDATE reports SET status=?, date_resolved=? WHERE id=?",
            (new_status, datetime.now().strftime("%Y-%m-%d %H:%M"), report_id),
        )
    else:
        conn.execute("UPDATE reports SET status=? WHERE id=?", (new_status, report_id))
    conn.commit()
    conn.close()

    computer_id = report["computer_id"]
    if new_status == "Resolved":
        log_activity(f"{computer_id} report resolved", category="report_resolved", computer_id=computer_id)
        remaining = get_open_report_count(computer_id)
        if remaining == 0:
            update_status(computer_id, "Working", reason="No open reports remain", changed_by=changed_by or "System")
    else:
        log_activity(f"{computer_id} report status changed to {new_status}", category="report_new", computer_id=computer_id)
    return True


def resolve_report(report_id):
    """Kept for backward compatibility - jumps a report straight to Resolved."""
    return update_report_status(report_id, "Resolved")


def update_report_priority(report_id, priority):
    conn = get_connection()
    conn.execute("UPDATE reports SET priority=? WHERE id=?", (priority, report_id))
    conn.commit()
    conn.close()


def get_recent_open_reports(limit=5):
    conn = get_connection()
    rows = conn.execute(
        "SELECT reports.*, computers.lab, computers.number FROM reports "
        "JOIN computers ON reports.computer_id = computers.computer_id "
        "WHERE reports.status!='Resolved' ORDER BY reports.date_reported DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def get_issue_type_counts(lab_id=None):
    conn = get_connection()
    if lab_id:
        rows = conn.execute(
            "SELECT issue_type, COUNT(*) as total FROM reports "
            "JOIN computers ON reports.computer_id = computers.computer_id "
            "WHERE computers.laboratory_id=? "
            "GROUP BY issue_type ORDER BY total DESC", (lab_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT issue_type, COUNT(*) as total FROM reports GROUP BY issue_type ORDER BY total DESC"
        ).fetchall()
    conn.close()
    return rows


# ---------- MAINTENANCE ----------

def add_maintenance(computer_id, report_id, technician, problem, diagnosis,
                     action_taken, parts_replaced, start_date, completion_date, result, remarks):
    conn = get_connection()
    conn.execute(
        "INSERT INTO maintenance (computer_id, report_id, technician, problem, diagnosis, "
        "action_taken, parts_replaced, start_date, completion_date, result, remarks) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (computer_id, report_id, technician, problem, diagnosis, action_taken,
         parts_replaced, start_date, completion_date, result, remarks),
    )
    conn.commit()
    conn.close()
    log_activity(f"Maintenance record added for {computer_id}", category="maintenance", computer_id=computer_id)


def get_maintenance_for_computer(computer_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM maintenance WHERE computer_id=? ORDER BY start_date DESC, id DESC", (computer_id,)
    ).fetchall()
    conn.close()
    return rows


def get_last_maintenance(computer_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM maintenance WHERE computer_id=? ORDER BY start_date DESC, id DESC LIMIT 1", (computer_id,)
    ).fetchone()
    conn.close()
    return row


def get_reports_for_print(computer_id=None, lab_id=None):
    """Every report, joined with its most recent linked maintenance record (if any),
    for the printable report log - this is what actually supplies 'how it was fixed'
    and 'who fixed it' when that information was recorded."""
    conn = get_connection()
    query = (
        "SELECT reports.*, computers.lab, computers.number, "
        "laboratories.location AS lab_location, "
        "m.technician, m.diagnosis, m.action_taken, m.result, m.completion_date "
        "FROM reports "
        "JOIN computers ON reports.computer_id = computers.computer_id "
        "LEFT JOIN laboratories ON computers.laboratory_id = laboratories.id "
        "LEFT JOIN maintenance m ON m.id = ("
        "  SELECT id FROM maintenance WHERE maintenance.report_id = reports.id "
        "  ORDER BY start_date DESC, id DESC LIMIT 1"
        ") "
    )
    conditions = []
    params = []
    if computer_id:
        conditions.append("reports.computer_id=?")
        params.append(computer_id)
    if lab_id:
        conditions.append("computers.laboratory_id=?")
        params.append(lab_id)
    if conditions:
        query += "WHERE " + " AND ".join(conditions) + " "
    query += "ORDER BY reports.date_reported DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


# ---------- DASHBOARD STATS ----------

def get_dashboard_stats():
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM computers").fetchone()[0]
    working = conn.execute("SELECT COUNT(*) FROM computers WHERE status='Working'").fetchone()[0]
    under_repair = conn.execute("SELECT COUNT(*) FROM computers WHERE status='Under Repair'").fetchone()[0]
    offline = conn.execute("SELECT COUNT(*) FROM computers WHERE status='Offline'").fetchone()[0]
    open_reports = conn.execute("SELECT COUNT(*) FROM reports WHERE status!='Resolved'").fetchone()[0]
    critical_reports = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE status!='Resolved' AND priority='Critical'"
    ).fetchone()[0]
    resolved_reports = conn.execute("SELECT COUNT(*) FROM reports WHERE status='Resolved'").fetchone()[0]
    conn.close()

    health = round((working / total) * 100) if total else 0

    return {
        "total": total,
        "working": working,
        "under_repair": under_repair,
        "offline": offline,
        "open_reports": open_reports,
        "critical_reports": critical_reports,
        "resolved_reports": resolved_reports,
        "health": health,
    }


# ---------- SETTINGS (key/value store) ----------

def get_all_settings():
    """Return every setting as a plain dict, e.g. {'theme': 'light', ...}."""
    conn = get_connection()
    rows = conn.execute("SELECT setting_key, setting_value FROM settings").fetchall()
    conn.close()
    values = {row["setting_key"]: row["setting_value"] for row in rows}
    # Fill in any default that might be missing (e.g. after an app update
    # added a new setting) without ever overwriting a saved value.
    for key, default in DEFAULT_SETTINGS.items():
        values.setdefault(key, default)
    return values


def get_setting(key):
    return get_all_settings().get(key, DEFAULT_SETTINGS.get(key))


def update_settings(values: dict):
    """Save one or more settings at once. `values` is {key: new_value}."""
    conn = get_connection()
    for key, value in values.items():
        conn.execute(
            "INSERT INTO settings (setting_key, setting_value) VALUES (?, ?) "
            "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
            (key, str(value)),
        )
    conn.commit()
    conn.close()


# ---------- USERNAME ----------

def update_username(old_username, new_username):
    conn = get_connection()
    conn.execute("UPDATE users SET username=? WHERE username=?", (new_username, old_username))
    conn.commit()
    conn.close()


def username_taken(username):
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return row is not None


# ---------- LABORATORIES (real physical locations; computers belong to these) ----------

def add_laboratory(name, location=None, description=None, code=None, building=None, floor=None, room=None, status="Active"):
    # location is auto-composed from building/floor/room when those are given,
    # so "Main Building - 2nd Floor - Room 201" never has to be typed twice.
    if not location and (building or floor or room):
        location = " - ".join(p for p in [building, floor, room] if p)
    conn = get_connection()
    conn.execute(
        "INSERT INTO laboratories (name, location, description, code, building, floor, room, status, date_created) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, location, description, code, building, floor, room, status or "Active",
         datetime.now().strftime("%Y-%m-%d %H:%M")),
    )
    conn.commit()
    conn.close()


def update_laboratory(lab_id, name, location=None, description=None, code=None,
                       building=None, floor=None, room=None, status="Active"):
    # Same auto-compose rule as add_laboratory, so editing stays consistent
    # with how the record was first created.
    if not location and (building or floor or room):
        location = " - ".join(p for p in [building, floor, room] if p)
    conn = get_connection()
    conn.execute(
        "UPDATE laboratories SET name=?, location=?, description=?, code=?, "
        "building=?, floor=?, room=?, status=? WHERE id=?",
        (name, location, description, code, building, floor, room, status or "Active", lab_id),
    )
    conn.commit()
    conn.close()


def get_laboratories():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM laboratories ORDER BY name").fetchall()
    conn.close()
    return rows


def get_laboratory(lab_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM laboratories WHERE id=?", (lab_id,)).fetchone()
    conn.close()
    return row


def get_laboratory_by_name(name):
    conn = get_connection()
    row = conn.execute("SELECT * FROM laboratories WHERE name=?", (name,)).fetchone()
    conn.close()
    return row


def get_computers_by_laboratory(lab_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM computers WHERE laboratory_id=? ORDER BY number", (lab_id,)
    ).fetchall()
    conn.close()
    return rows


def get_lab_stats(lab_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='Working' THEN 1 ELSE 0 END) AS working, "
        "SUM(CASE WHEN status='Under Repair' THEN 1 ELSE 0 END) AS under_repair, "
        "SUM(CASE WHEN status='Offline' THEN 1 ELSE 0 END) AS offline "
        "FROM computers WHERE laboratory_id=?", (lab_id,)
    ).fetchone()
    conn.close()
    return {
        "total": row["total"] or 0,
        "working": row["working"] or 0,
        "under_repair": row["under_repair"] or 0,
        "offline": row["offline"] or 0,
    }


def delete_laboratory(lab_id):
    conn = get_connection()
    conn.execute("DELETE FROM laboratories WHERE id=?", (lab_id,))
    conn.commit()
    conn.close()


def get_lab_computer_counts():
    """Computer count per lab name, computed live so it can never go stale."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT lab, COUNT(*) as total FROM computers GROUP BY lab"
    ).fetchall()
    conn.close()
    return {row["lab"]: row["total"] for row in rows}


# ---------- ACTIVITY LOG (Settings > Security) ----------

def log_activity(event_type, username=None, detail=None, category=None, computer_id=None):
    """Record an event for the dashboard Activity Feed / Settings Security log.
    Never pass a password into `detail`. `category` drives which icon the
    activity feed shows (see get_recent_activity callers in app.py)."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (event_type, username, timestamp, detail, category, computer_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (event_type, username, datetime.now().strftime("%Y-%m-%d %H:%M"), detail, category, computer_id),
    )
    conn.commit()
    conn.close()


def get_recent_activity(limit=10):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows


# ---------- MAINTENANCE / DATA MANAGEMENT ----------

def delete_old_resolved_reports(days=90):
    """Delete Resolved reports older than `days`. Returns how many were removed."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM reports WHERE status='Resolved' AND date_resolved IS NOT NULL AND date_resolved < ?",
        (cutoff,),
    )
    removed = cur.rowcount
    conn.commit()
    conn.close()
    return removed


def get_all_reports_raw():
    """All report rows with computer info, for CSV export."""
    return get_reports(status=None)


# ---------- DANGER ZONE ----------
# Each of these is only ever called after the route has verified the admin
# typed the exact confirmation phrase - see app.py. None of them touch the
# `users` or `settings` tables, so the admin account is never locked out.

def delete_all_reports():
    conn = get_connection()
    conn.execute("DELETE FROM reports")
    conn.commit()
    conn.close()
    # No open reports remain, so every computer that was "Under Repair"
    # only because of a report should go back to "Working".
    conn = get_connection()
    conn.execute("UPDATE computers SET status='Working' WHERE status='Under Repair'")
    conn.commit()
    conn.close()


def reset_all_computers():
    """Deletes every computer AND their reports (a report cannot outlive its computer)."""
    conn = get_connection()
    conn.execute("DELETE FROM reports")
    conn.execute("DELETE FROM computers")
    conn.commit()
    conn.close()


def reset_application_data():
    """Full data wipe: all computers and reports. Users and settings are kept
    on purpose, so the administrator is never locked out of their own system."""
    reset_all_computers()
