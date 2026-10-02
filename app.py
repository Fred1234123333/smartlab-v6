"""
app.py
SmartLab: QR-Based Computer Laboratory Monitoring System (Flask version)
"""

import os
import csv
import io
import zipfile
import shutil
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, abort,
    send_file, Response, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import database as db
import qr_utils

app = Flask(__name__)
app.secret_key = os.environ.get("SMARTLAB_SECRET_KEY", "dev-key-change-this-in-production")

# ---------------------------------------------------------------------------
# CSRF PROTECTION
# ---------------------------------------------------------------------------
# Every state-changing form (POST/PUT/PATCH/DELETE) in the app is protected
# automatically once this is enabled - no per-route code needed. Each
# template's forms just need {{ csrf_token() }} in a hidden field, which
# CSRFProtect makes globally available to every Jinja template below.
app.config["WTF_CSRF_TIME_LIMIT"] = None  # tokens don't expire mid-session
csrf = CSRFProtect(app)

# ---------------------------------------------------------------------------
# RATE LIMITING
# ---------------------------------------------------------------------------
# Applied narrowly, not globally: the login form (brute-force password
# guessing) and the public report form (spam) are the two endpoints that
# accept input from people who aren't authenticated, so they're the ones
# worth protecting. Everything else already requires a valid login session.
# Storage is in-memory, which is fine for the single-process dev server this
# project runs on; a real multi-worker deployment would swap in Redis.
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://", default_limits=[])

APP_VERSION = "1.0.0"

db.initialize_database()


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_notifications():
    """Makes the notification bell + current settings available to every
    template without passing them into every single render_template() call."""
    app_settings = db.get_all_settings()
    context = {"app_settings": app_settings}
    if session.get("username"):
        recent = db.get_recent_open_reports(limit=5)
        # The "New Report Notifications" setting only hides the numeric badge;
        # the dashboard/reports pages always show the real, unfiltered data.
        show_badge = app_settings.get("notif_new_report", "on") == "on"
        open_count = len(db.get_reports(status="Open"))
        context["nav_open_count"] = open_count if show_badge else 0
        context["nav_recent_reports"] = recent
    return context


# ---------------------------------------------------------------------------
# PUBLIC PAGES
# ---------------------------------------------------------------------------

@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_user(username)
        if user and check_password_hash(user["password_hash"], password):
            session["username"] = username
            session["just_logged_in"] = True

            # Settings > Security: Auto Logout + Session Timeout.
            # When Auto Logout is on, the session expires after the chosen
            # number of minutes of inactivity. When it's off, the session
            # instead lasts until the browser itself is closed.
            auto_logout = db.get_setting("auto_logout") == "on"
            if auto_logout:
                minutes = int(db.get_setting("session_timeout") or 30)
                app.permanent_session_lifetime = timedelta(minutes=minutes)
                session.permanent = True
            else:
                session.permanent = False

            db.log_activity("Successful login", username=username)
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)

        db.log_activity("Failed login attempt", username=username or None)
        flash("Incorrect username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    if session.get("username"):
        db.log_activity("Successful logout", username=session["username"])
    session.clear()
    return redirect(url_for("landing"))


@app.route("/qr/<computer_id>")
def public_status(computer_id):
    computer = db.get_computer(computer_id)
    if not computer:
        abort(404)
    recent_reports = db.get_reports_for_computer(computer_id)[:5]
    return render_template(
        "public_status.html",
        computer=computer,
        recent_reports=recent_reports,
        issue_types=db.ISSUE_TYPES,
    )


@app.route("/qr/<computer_id>/report", methods=["POST"])
@limiter.limit("20 per hour")
def submit_report(computer_id):
    computer = db.get_computer(computer_id)
    if not computer:
        abort(404)

    issue_type = request.form.get("issue_type", "").strip()
    description = request.form.get("description", "").strip()
    reporter_input = request.form.get("reporter", "").strip()

    allow_anonymous = db.get_setting("allow_anonymous_reports") == "on"
    if not allow_anonymous and not reporter_input:
        flash("Please enter your name — anonymous reports are turned off for this lab.", "error")
        return redirect(url_for("public_status", computer_id=computer_id))
    reporter = reporter_input or "Anonymous"

    if issue_type not in db.ISSUE_TYPES:
        flash("Please select a valid issue type.", "error")
        return redirect(url_for("public_status", computer_id=computer_id))

    report_id = db.create_report(computer_id, issue_type, description, reporter)
    return redirect(url_for("report_confirmation", report_id=report_id))


@app.route("/report/confirmation/<int:report_id>")
def report_confirmation(report_id):
    report = db.get_report(report_id)
    if not report:
        abort(404)
    computer = db.get_computer(report["computer_id"])
    return render_template("report_confirmation.html", report=report, computer=computer)


# ---------------------------------------------------------------------------
# ADMIN: DASHBOARD
# ---------------------------------------------------------------------------

@app.route("/admin/dashboard")
@login_required
def dashboard():
    stats = db.get_dashboard_stats()
    issue_counts = db.get_issue_type_counts()
    max_issue = max([c["total"] for c in issue_counts], default=1)
    recent_activity = db.get_recent_activity(limit=8)
    show_welcome = session.pop("just_logged_in", False)  # only fires once, right after login
    return render_template(
        "dashboard.html", stats=stats, issue_counts=issue_counts, max_issue=max_issue,
        recent_activity=[_serialize_activity(a) for a in recent_activity],
        show_welcome=show_welcome,
    )


# ---------------------------------------------------------------------------
# ADMIN: QR SCANNER (looks up a computer by scanning its own QR code)
# ---------------------------------------------------------------------------

@app.route("/admin/scan")
@login_required
def scan_qr_page():
    return render_template("scan.html")


# ---------------------------------------------------------------------------
# ADMIN: COMPUTERS
# ---------------------------------------------------------------------------

@app.route("/admin/computers")
@login_required
def computers_list():
    # Initial status/search/lab are read from the URL only to pre-select the UI
    # state on first paint (so a bookmarked/shared link still opens filtered).
    # All computers are always sent to the template; filtering afterward
    # happens instantly client-side (see computers.html) with no reload.
    initial_status = request.args.get("status", "All")
    initial_search = request.args.get("search", "").strip()
    initial_lab = request.args.get("lab", "All")

    laboratories = db.get_laboratories()
    all_computers = db.get_all_computers_with_lab()

    open_counts = {c["computer_id"]: db.get_open_report_count(c["computer_id"]) for c in all_computers}

    # Group computers by laboratory_id, preserving the laboratories' own name
    # order; any computer with no (or a deleted) laboratory link is collected
    # separately instead of being silently dropped.
    groups = []
    for lab in laboratories:
        lab_computers = [c for c in all_computers if c["laboratory_id"] == lab["id"]]
        groups.append({
            "lab": lab,
            "computers": lab_computers,
            "stats": db.get_lab_stats(lab["id"]),
        })
    unassigned = [c for c in all_computers if not c["laboratory_id"]]
    if unassigned:
        groups.append({
            "lab": None,
            "computers": unassigned,
            "stats": {
                "total": len(unassigned),
                "working": sum(1 for c in unassigned if c["status"] == "Working"),
                "under_repair": sum(1 for c in unassigned if c["status"] == "Under Repair"),
                "offline": sum(1 for c in unassigned if c["status"] == "Offline"),
            },
        })

    return render_template(
        "computers.html",
        groups=groups,
        laboratories=laboratories,
        has_computers=len(all_computers) > 0,
        open_counts=open_counts,
        status_filter=initial_status,
        search=initial_search,
        lab_filter=initial_lab,
        status_options=["All"] + db.STATUS_OPTIONS,
    )


@app.route("/admin/computers/add", methods=["GET", "POST"])
@login_required
def add_computer():
    laboratories = db.get_laboratories()
    if request.method == "POST":
        laboratory_id_raw = request.form.get("laboratory_id", "").strip()
        number = request.form.get("number", "").strip()
        computer_id = request.form.get("computer_id", "").strip().upper()

        if not laboratory_id_raw or not number or not computer_id:
            flash("Please fill in all fields.", "error")
            return render_template("add_computer.html", laboratories=laboratories)

        lab_row = db.get_laboratory(int(laboratory_id_raw)) if laboratory_id_raw.isdigit() else None
        if not lab_row:
            flash("Please select a valid laboratory.", "error")
            return render_template("add_computer.html", laboratories=laboratories)

        if db.computer_exists(computer_id):
            flash(f"Computer ID '{computer_id}' already exists.", "error")
            return render_template("add_computer.html", laboratories=laboratories)

        try:
            number_int = int(number)
        except ValueError:
            flash("Computer number must be a number.", "error")
            return render_template("add_computer.html", laboratories=laboratories)

        qr_path = qr_utils.generate_qr(computer_id, request.host_url)
        db.add_computer(computer_id, lab_row["name"], number_int, qr_path, laboratory_id=lab_row["id"])
        flash(f"Computer '{computer_id}' added successfully.", "success")
        return redirect(url_for("computer_detail", computer_id=computer_id))

    return render_template("add_computer.html", laboratories=laboratories)


@app.route("/admin/laboratories")
@login_required
def laboratories_list():
    labs = db.get_laboratories()
    lab_stats = {lab["id"]: db.get_lab_stats(lab["id"]) for lab in labs}
    return render_template("laboratories.html", laboratories=labs, lab_stats=lab_stats)


@app.route("/admin/laboratories/<int:lab_id>")
@login_required
def laboratory_detail(lab_id):
    lab = db.get_laboratory(lab_id)
    if not lab:
        abort(404)
    stats = db.get_lab_stats(lab_id)
    computers = db.get_computers_by_laboratory(lab_id)
    computer_ids = [c["computer_id"] for c in computers]
    open_reports = [r for r in db.get_open_reports() if r["computer_id"] in computer_ids]
    return render_template("laboratory_detail.html", lab=lab, stats=stats,
                            computers=computers, open_reports=open_reports)


@app.route("/admin/computers/<computer_id>")
@login_required
def computer_detail(computer_id):
    computer = db.get_computer_with_lab(computer_id)
    if not computer:
        abort(404)
    reports = db.get_reports_for_computer(computer_id)
    open_count = sum(1 for r in reports if r["status"] != "Resolved")
    status_history = db.get_status_history(computer_id)
    maintenance_records = db.get_maintenance_for_computer(computer_id)
    last_maintenance = db.get_last_maintenance(computer_id)
    return render_template(
        "computer_detail.html",
        computer=computer,
        reports=reports,
        open_count=open_count,
        status_options=db.STATUS_OPTIONS,
        status_history=status_history,
        maintenance_records=maintenance_records,
        last_maintenance=last_maintenance,
        priority_options=db.PRIORITY_OPTIONS,
        report_status_options=db.REPORT_STATUS_OPTIONS,
        maintenance_result_options=db.MAINTENANCE_RESULT_OPTIONS,
        laboratories=db.get_laboratories(),
    )


@app.route("/admin/computers/<computer_id>/status", methods=["POST"])
@login_required
def change_status(computer_id):
    new_status = request.form.get("status")
    reason = request.form.get("reason", "").strip() or None
    if new_status not in db.STATUS_OPTIONS:
        flash("Invalid status.", "error")
    else:
        db.update_status(computer_id, new_status, reason=reason, changed_by=session.get("username"))
        db.log_activity(f"{computer_id} status changed to {new_status}", category="status_manual", computer_id=computer_id)
        flash(f"{computer_id} status updated to {new_status}.", "success")
    return redirect(url_for("computer_detail", computer_id=computer_id))


@app.route("/admin/computers/<computer_id>/specs", methods=["POST"])
@login_required
def update_specs(computer_id):
    if not db.computer_exists(computer_id):
        abort(404)
    specs = {field: request.form.get(field, "").strip() for field in db.SPEC_FIELDS}
    db.update_computer_specs(computer_id, specs)
    db.log_activity(f"{computer_id} specifications updated", category="status_manual", computer_id=computer_id)
    flash("Specifications saved.", "success")
    return redirect(url_for("computer_detail", computer_id=computer_id))


@app.route("/admin/computers/<computer_id>/edit", methods=["POST"])
@login_required
def edit_computer(computer_id):
    computer = db.get_computer(computer_id)
    if not computer:
        abort(404)
    new_id = request.form.get("computer_id", "").strip().upper()
    laboratory_id = request.form.get("laboratory_id", "").strip()
    number = request.form.get("number", "").strip()

    if not new_id or not laboratory_id or not number.isdigit():
        flash("Computer ID, laboratory, and workstation number are all required.", "error")
        return redirect(url_for("computer_detail", computer_id=computer_id))

    if new_id != computer_id and db.computer_exists(new_id):
        flash(f"Computer ID '{new_id}' is already in use by another computer.", "error")
        return redirect(url_for("computer_detail", computer_id=computer_id))

    new_qr_path = None
    old_qr_full_path = None
    if new_id != computer_id:
        # The ID is encoded in the QR image itself, so a renamed computer
        # needs a freshly generated code under its new name.
        new_qr_path = qr_utils.generate_qr(new_id, request.host_url)
        if computer["qr_path"]:
            old_qr_full_path = os.path.join(app.root_path, "static", computer["qr_path"])

    db.update_computer(computer_id, new_id, int(laboratory_id), int(number), new_qr_path=new_qr_path)

    if old_qr_full_path and os.path.exists(old_qr_full_path):
        os.remove(old_qr_full_path)

    db.log_activity(f"{computer_id} edited" + (f" (renamed to {new_id})" if new_id != computer_id else ""),
                     category="status_manual", computer_id=new_id)
    flash("Computer updated.", "success")
    return redirect(url_for("computer_detail", computer_id=new_id))


@app.route("/admin/computers/<computer_id>/maintenance", methods=["POST"])
@login_required
def add_maintenance_record(computer_id):
    if not db.computer_exists(computer_id):
        abort(404)
    report_id = request.form.get("report_id", "").strip()
    db.add_maintenance(
        computer_id=computer_id,
        report_id=int(report_id) if report_id.isdigit() else None,
        technician=request.form.get("technician", "").strip(),
        problem=request.form.get("problem", "").strip(),
        diagnosis=request.form.get("diagnosis", "").strip(),
        action_taken=request.form.get("action_taken", "").strip(),
        parts_replaced=request.form.get("parts_replaced", "").strip(),
        start_date=request.form.get("start_date", "").strip(),
        completion_date=request.form.get("completion_date", "").strip() or None,
        result=request.form.get("result", "").strip(),
        remarks=request.form.get("remarks", "").strip(),
    )
    flash("Maintenance record added.", "success")
    return redirect(url_for("computer_detail", computer_id=computer_id))


@app.route("/admin/computers/<computer_id>/delete", methods=["POST"])
@login_required
def delete_computer(computer_id):
    db.delete_computer(computer_id)
    flash(f"{computer_id} was deleted.", "success")
    return redirect(url_for("computers_list"))


# ---------------------------------------------------------------------------
# ADMIN: REPORTS
# ---------------------------------------------------------------------------

@app.route("/admin/reports")
@login_required
def reports_list():
    tab = request.args.get("tab", "open")
    page = max(1, request.args.get("page", 1, type=int))
    lab_param = request.args.get("lab", "All")
    lab_id = int(lab_param) if lab_param.isdigit() else None
    per_page = db.REPORTS_PER_PAGE
    if tab == "open":
        reports = db.get_open_reports(page=page, per_page=per_page, lab_id=lab_id)
        total = db.count_open_reports(lab_id=lab_id)
    else:
        reports = db.get_reports(status="Resolved", page=page, per_page=per_page, lab_id=lab_id)
        total = db.count_reports(status="Resolved", lab_id=lab_id)
    total_pages = max(1, (total + per_page - 1) // per_page)

    laboratories = db.get_laboratories()
    # Group this page's reports by laboratory for display; a report whose
    # computer's laboratory was removed falls under "Unassigned" rather than
    # being dropped, same rule as the Computers page.
    lab_by_id = {lab["id"]: lab for lab in laboratories}
    groups_map = {}
    order = []
    for r in reports:
        key = r["laboratory_id"] or "unassigned"
        if key not in groups_map:
            groups_map[key] = []
            order.append(key)
        groups_map[key].append(r)
    groups = []
    for key in order:
        groups.append({
            "lab": lab_by_id.get(key) if key != "unassigned" else None,
            "reports": groups_map[key],
        })

    return render_template(
        "reports.html", reports=reports, groups=groups, tab=tab,
        laboratories=laboratories, lab_filter=lab_param,
        report_status_options=db.REPORT_STATUS_OPTIONS,
        priority_options=db.PRIORITY_OPTIONS,
        page=page, total_pages=total_pages, total=total,
    )


@app.route("/admin/reports/<int:report_id>/resolve", methods=["POST"])
@login_required
def resolve_report(report_id):
    db.resolve_report(report_id)
    flash("Report marked as resolved.", "success")
    return redirect(url_for("reports_list", tab="open"))


@app.route("/admin/reports/<int:report_id>/workflow", methods=["POST"])
@login_required
def update_report_workflow(report_id):
    new_status = request.form.get("status")
    if new_status not in db.REPORT_STATUS_OPTIONS:
        flash("Invalid report status.", "error")
    else:
        db.update_report_status(report_id, new_status, changed_by=session.get("username"))
        flash(f"Report status updated to {new_status}.", "success")
    return redirect(request.referrer or url_for("reports_list"))


@app.route("/admin/reports/<int:report_id>/priority", methods=["POST"])
@login_required
def update_priority(report_id):
    priority = request.form.get("priority")
    if priority not in db.PRIORITY_OPTIONS:
        flash("Invalid priority.", "error")
    else:
        db.update_report_priority(report_id, priority)
        flash(f"Priority set to {priority}.", "success")
    return redirect(request.referrer or url_for("reports_list"))


# ---------------------------------------------------------------------------
# JSON API - powers live/interactive updates without full page reloads.
# Every value returned here is read straight from the database; nothing is
# invented client-side. Kept deliberately small (one combined dashboard
# endpoint) so the periodic polling stays cheap.
# ---------------------------------------------------------------------------

ACTIVITY_ICONS = {
    "status_manual": "status",
    "report_new": "alert",
    "report_resolved": "check",
    "computer_added": "plus",
    "login": "login",
    "maintenance": "wrench",
}


def _serialize_activity(row):
    return {
        "event_type": row["event_type"],
        "username": row["username"],
        "timestamp": row["timestamp"],
        "category": row["category"],
        "computer_id": row["computer_id"],
        "icon": ACTIVITY_ICONS.get(row["category"], "dot"),
    }


@app.route("/api/dashboard-data")
@login_required
def api_dashboard_data():
    stats = db.get_dashboard_stats()
    issue_counts = db.get_issue_type_counts()
    activity = db.get_recent_activity(limit=8)
    from datetime import datetime as _dt
    return jsonify({
        "stats": stats,
        "issue_counts": [{"issue_type": r["issue_type"], "total": r["total"]} for r in issue_counts],
        "activity": [_serialize_activity(a) for a in activity],
        "server_time": _dt.now().strftime("%I:%M:%S %p"),
    })


@app.route("/api/computers/<computer_id>/status", methods=["POST"])
@login_required
def api_change_status(computer_id):
    computer = db.get_computer(computer_id)
    if not computer:
        return jsonify({"success": False, "error": "Computer not found."}), 404

    new_status = (request.get_json(silent=True) or request.form).get("status")
    if new_status not in db.STATUS_OPTIONS:
        return jsonify({"success": False, "error": "Invalid status."}), 400

    db.update_status(computer_id, new_status, changed_by=session.get("username"))
    db.log_activity(f"{computer_id} status changed to {new_status}", category="status_manual", computer_id=computer_id)
    return jsonify({
        "success": True,
        "computer_id": computer_id,
        "new_status": new_status,
        "stats": db.get_dashboard_stats(),
    })


@app.route("/api/reports/<int:report_id>/resolve", methods=["POST"])
@login_required
def api_resolve_report(report_id):
    report = db.get_report(report_id)
    if not report:
        return jsonify({"success": False, "error": "Report not found."}), 404

    db.resolve_report(report_id)
    return jsonify({
        "success": True,
        "report_id": report_id,
        "computer_id": report["computer_id"],
        "stats": db.get_dashboard_stats(),
    })


# ---------------------------------------------------------------------------
# ADMIN: STATISTICS
# ---------------------------------------------------------------------------

@app.route("/admin/statistics")
@login_required
def statistics():
    stats = db.get_dashboard_stats()
    issue_counts = db.get_issue_type_counts()
    max_issue = max([c["total"] for c in issue_counts], default=1)

    laboratories = db.get_laboratories()
    lab_stats = []
    for lab in laboratories:
        computer_stats = db.get_lab_stats(lab["id"])
        lab_issue_counts = db.get_issue_type_counts(lab_id=lab["id"])
        lab_max_issue = max([c["total"] for c in lab_issue_counts], default=1)
        lab_stats.append({
            "lab": lab,
            "computers": computer_stats,
            "health": round((computer_stats["working"] / computer_stats["total"]) * 100) if computer_stats["total"] else 0,
            "open_reports": db.count_open_reports(lab_id=lab["id"]),
            "resolved_reports": db.count_reports(status="Resolved", lab_id=lab["id"]),
            "issue_counts": lab_issue_counts,
            "max_issue": lab_max_issue,
        })

    return render_template(
        "statistics.html", stats=stats, issue_counts=issue_counts, max_issue=max_issue,
        lab_stats=lab_stats, total_laboratories=len(laboratories),
    )


# ---------------------------------------------------------------------------
# ADMIN: SETTINGS
# ---------------------------------------------------------------------------

def _settings_redirect(tab):
    return redirect(url_for("settings", tab=tab))


@app.route("/admin/settings")
@login_required
def settings():
    tab = request.args.get("tab", "general")
    settings_values = db.get_all_settings()
    labs = db.get_laboratories()
    lab_counts = db.get_lab_computer_counts()
    recent_activity = db.get_recent_activity(limit=10)
    dashboard_stats = db.get_dashboard_stats()

    # A few live system checks for the System / Security cards - computed on
    # every visit rather than stored, so they can never show stale info.
    db_connected = True
    try:
        db.get_connection().close()
    except Exception:
        db_connected = False
    qr_storage_ok = os.path.isdir(qr_utils.QR_FOLDER) and os.access(qr_utils.QR_FOLDER, os.W_OK)

    return render_template(
        "settings.html",
        active_tab=tab,
        s=settings_values,
        labs=labs,
        lab_counts=lab_counts,
        recent_activity=recent_activity,
        dashboard_stats=dashboard_stats,
        db_connected=db_connected,
        qr_storage_ok=qr_storage_ok,
        qr_size_options=list(db.QR_SIZE_OPTIONS.keys()),
        accent_colors=db.ACCENT_COLORS,
        session_timeout_options=db.SESSION_TIMEOUT_OPTIONS,
        app_version=APP_VERSION,
    )


@app.route("/admin/settings/general", methods=["POST"])
@login_required
def settings_general():
    db.update_settings({
        "system_name": request.form.get("system_name", "SmartLab").strip() or "SmartLab",
        "system_description": request.form.get("system_description", "").strip(),
        "admin_email": request.form.get("admin_email", "").strip(),
        "default_lab": request.form.get("default_lab", "").strip(),
        "allow_anonymous_reports": "on" if request.form.get("allow_anonymous_reports") == "on" else "off",
    })
    flash("General settings saved.", "success")
    return _settings_redirect("general")


@app.route("/admin/settings/account/username", methods=["POST"])
@login_required
def settings_change_username():
    new_username = request.form.get("new_username", "").strip()
    current_password = request.form.get("current_password_for_username", "")
    user = db.get_user(session["username"])

    if not check_password_hash(user["password_hash"], current_password):
        flash("Current password is incorrect.", "error")
    elif not new_username:
        flash("Please enter a new username.", "error")
    elif new_username != session["username"] and db.username_taken(new_username):
        flash(f"Username '{new_username}' is already taken.", "error")
    else:
        db.update_username(session["username"], new_username)
        session["username"] = new_username
        flash("Username updated successfully.", "success")
    return _settings_redirect("account")


@app.route("/admin/settings/account/password", methods=["POST"])
@login_required
def settings_change_password():
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    user = db.get_user(session["username"])
    if not check_password_hash(user["password_hash"], current):
        flash("Current password is incorrect.", "error")
    elif len(new) < 6:
        flash("New password must be at least 6 characters.", "error")
    elif new != confirm:
        flash("New password and confirmation do not match.", "error")
    else:
        db.update_password(session["username"], generate_password_hash(new))
        flash("Password updated successfully.", "success")
    return _settings_redirect("account")


@app.route("/admin/settings/appearance", methods=["POST"])
@login_required
def settings_appearance():
    theme = request.form.get("theme", "light")
    accent = request.form.get("accent_color", "Strong Green")
    db.update_settings({
        "theme": theme if theme in ("light", "dark", "system") else "light",
        "accent_color": accent if accent in db.ACCENT_COLORS else "Strong Green",
        "compact_mode": "on" if request.form.get("compact_mode") == "on" else "off",
        "animations": "on" if request.form.get("animations") == "on" else "off",
    })
    flash("Appearance settings saved.", "success")
    return _settings_redirect("appearance")


@app.route("/api/theme", methods=["POST"])
@login_required
def api_set_theme():
    """Lightweight endpoint for the topbar quick-toggle - updates only the
    theme, leaving accent_color/compact_mode/animations untouched (unlike
    the full Settings > Appearance form save above)."""
    theme = (request.get_json(silent=True) or {}).get("theme", "")
    if theme not in ("light", "dark", "system"):
        return jsonify({"error": "invalid theme"}), 400
    db.update_settings({"theme": theme})
    return jsonify({"success": True, "theme": theme})


@app.route("/admin/settings/laboratory/add", methods=["POST"])
@login_required
def settings_add_laboratory():
    name = request.form.get("lab_name", "").strip()
    code = request.form.get("lab_code", "").strip()
    building = request.form.get("lab_building", "").strip()
    floor = request.form.get("lab_floor", "").strip()
    room = request.form.get("lab_room", "").strip()
    location = request.form.get("lab_location", "").strip()
    description = request.form.get("lab_description", "").strip()
    if not name:
        flash("Laboratory name is required.", "error")
    else:
        try:
            db.add_laboratory(name, location=location or None, description=description or None,
                               code=code or None, building=building or None, floor=floor or None,
                               room=room or None)
            flash(f"Laboratory '{name}' added.", "success")
        except Exception:
            flash(f"A laboratory named '{name}' already exists.", "error")
    return _settings_redirect("laboratory")


@app.route("/admin/settings/laboratory/<int:lab_id>/edit", methods=["POST"])
@login_required
def settings_edit_laboratory(lab_id):
    if not db.get_laboratory(lab_id):
        abort(404)
    name = request.form.get("lab_name", "").strip()
    code = request.form.get("lab_code", "").strip()
    building = request.form.get("lab_building", "").strip()
    floor = request.form.get("lab_floor", "").strip()
    room = request.form.get("lab_room", "").strip()
    location = request.form.get("lab_location", "").strip()
    description = request.form.get("lab_description", "").strip()
    if not name:
        flash("Laboratory name is required.", "error")
    else:
        try:
            db.update_laboratory(lab_id, name, location=location or None, description=description or None,
                                  code=code or None, building=building or None, floor=floor or None,
                                  room=room or None)
            flash(f"Laboratory '{name}' updated.", "success")
        except Exception:
            flash(f"A laboratory named '{name}' already exists.", "error")
    return _settings_redirect("laboratory")


@app.route("/admin/settings/laboratory/<int:lab_id>/delete", methods=["POST"])
@login_required
def settings_delete_laboratory(lab_id):
    db.delete_laboratory(lab_id)
    flash("Laboratory reference removed. Existing computers were not affected.", "success")
    return _settings_redirect("laboratory")


@app.route("/admin/settings/qr/size", methods=["POST"])
@login_required
def settings_qr_size():
    size = request.form.get("qr_size", "Small")
    if size not in db.QR_SIZE_OPTIONS:
        size = "Small"
    db.update_settings({"qr_size": size})
    flash(f"QR code size set to {size}. Use 'Regenerate All QR Codes' to apply it to existing computers.", "success")
    return _settings_redirect("qr")


@app.route("/admin/settings/qr/regenerate-all", methods=["POST"])
@login_required
def settings_regenerate_all_qr():
    box_size = db.QR_SIZE_OPTIONS.get(db.get_setting("qr_size"), 8)
    computers = db.get_all_computers()
    for c in computers:
        qr_utils.generate_qr(c["computer_id"], request.host_url, box_size=box_size)
    flash(f"Regenerated QR codes for all {len(computers)} computers.", "success")
    return _settings_redirect("qr")


@app.route("/admin/settings/qr/download-all")
@login_required
def settings_download_all_qr():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in db.get_all_computers():
            if c["qr_path"]:
                full_path = os.path.join(qr_utils.BASE_DIR, "static", c["qr_path"])
                if os.path.exists(full_path):
                    zf.write(full_path, arcname=f"{c['computer_id']}.png")
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="smartlab_qr_codes.zip", mimetype="application/zip")


@app.route("/admin/settings/qr/print")
@login_required
def settings_print_qr():
    computer_id = request.args.get("computer_id")
    if computer_id:
        one = db.get_computer(computer_id)
        computers = [one] if one else []
    else:
        computers = db.get_all_computers()
    return render_template("qr_print.html", computers=computers)


@app.route("/admin/reports/print")
@login_required
def print_reports():
    computer_id = request.args.get("computer_id")
    status = request.args.get("status")  # "open", "resolved", or blank for all
    lab_id_raw = request.args.get("lab_id")
    lab_id = int(lab_id_raw) if lab_id_raw and lab_id_raw.isdigit() else None
    reports = db.get_reports_for_print(computer_id=computer_id, lab_id=lab_id)
    if status == "open":
        reports = [r for r in reports if r["status"] != "Resolved"]
    elif status == "resolved":
        reports = [r for r in reports if r["status"] == "Resolved"]
    computer = db.get_computer(computer_id) if computer_id else None
    lab = db.get_laboratory(lab_id) if lab_id else None
    printed_at = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    return render_template("report_print.html", reports=reports, computer=computer, lab=lab, status=status, printed_at=printed_at)


@app.route("/admin/computers/<computer_id>/regenerate-qr", methods=["POST"])
@login_required
def regenerate_single_qr(computer_id):
    computer = db.get_computer(computer_id)
    if not computer:
        abort(404)
    box_size = db.QR_SIZE_OPTIONS.get(db.get_setting("qr_size"), 8)
    qr_utils.generate_qr(computer_id, request.host_url, box_size=box_size)
    flash(f"QR code regenerated for {computer_id}.", "success")
    return redirect(url_for("computer_detail", computer_id=computer_id))


@app.route("/admin/settings/security", methods=["POST"])
@login_required
def settings_security():
    timeout = request.form.get("session_timeout", "30")
    if timeout not in [str(t) for t in db.SESSION_TIMEOUT_OPTIONS]:
        timeout = "30"
    db.update_settings({
        "session_timeout": timeout,
        "auto_logout": "on" if request.form.get("auto_logout") == "on" else "off",
    })
    flash("Security settings saved. They take effect the next time you log in.", "success")
    return _settings_redirect("security")


@app.route("/admin/settings/notifications", methods=["POST"])
@login_required
def settings_notifications():
    db.update_settings({
        "notif_new_report": "on" if request.form.get("notif_new_report") == "on" else "off",
        "notif_resolved_report": "on" if request.form.get("notif_resolved_report") == "on" else "off",
        "notif_system_alerts": "on" if request.form.get("notif_system_alerts") == "on" else "off",
        "notif_sound": "on" if request.form.get("notif_sound") == "on" else "off",
    })
    flash("Notification settings saved.", "success")
    return _settings_redirect("notifications")


@app.route("/admin/settings/about", methods=["POST"])
@login_required
def settings_about():
    db.update_settings({
        "about_developers": request.form.get("about_developers", "").strip(),
        "about_course": request.form.get("about_course", "").strip(),
        "about_section": request.form.get("about_section", "").strip(),
        "about_school": request.form.get("about_school", "").strip(),
    })
    flash("About section updated.", "success")
    return _settings_redirect("about")


@app.route("/admin/settings/backup")
@login_required
def settings_backup():
    if not os.path.exists(db.DB_PATH):
        flash("No database file was found to back up.", "error")
        return _settings_redirect("system")
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(db.DB_PATH, as_attachment=True, download_name=f"smartlab_backup_{stamp}.db")


@app.route("/admin/settings/check-system", methods=["POST"])
@login_required
def settings_check_system():
    checks = []
    try:
        db.get_connection().close()
        checks.append("Database connection: OK")
    except Exception as e:
        checks.append(f"Database connection: FAILED ({e})")

    if os.path.isdir(qr_utils.QR_FOLDER) and os.access(qr_utils.QR_FOLDER, os.W_OK):
        checks.append("QR code storage: OK")
    else:
        checks.append("QR code storage: NOT WRITABLE")

    stats = db.get_dashboard_stats()
    checks.append(f"{stats['total']} computers, {stats['open_reports']} open reports")

    flash(" | ".join(checks), "success")
    return _settings_redirect("system")


@app.route("/admin/settings/clear-old-reports", methods=["POST"])
@login_required
def settings_clear_old_reports():
    if request.form.get("confirm_text") != "DELETE":
        flash("Type DELETE exactly to confirm clearing old reports.", "error")
        return _settings_redirect("system")
    removed = db.delete_old_resolved_reports(days=90)
    flash(f"Removed {removed} resolved report(s) older than 90 days.", "success")
    return _settings_redirect("system")


@app.route("/admin/settings/export/computers")
@login_required
def export_computers_csv():
    computers = db.get_all_computers()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Computer ID", "Laboratory", "Number", "Status", "Date Added"])
    for c in computers:
        writer.writerow([c["computer_id"], c["lab"], c["number"], c["status"], c["date_added"]])
    return Response(
        buffer.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=smartlab_computers.csv"},
    )


@app.route("/admin/settings/export/reports")
@login_required
def export_reports_csv():
    reports = db.get_all_reports_raw()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Report ID", "Computer ID", "Laboratory", "Issue Type", "Description",
                      "Reporter", "Date Reported", "Status", "Date Resolved"])
    for r in reports:
        writer.writerow([r["id"], r["computer_id"], r["lab"], r["issue_type"], r["description"],
                          r["reporter"], r["date_reported"], r["status"], r["date_resolved"]])
    return Response(
        buffer.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=smartlab_reports.csv"},
    )


# ---- Danger Zone: every action requires the admin to type DELETE exactly ----

@app.route("/admin/settings/danger/delete-reports", methods=["POST"])
@login_required
def danger_delete_reports():
    if request.form.get("confirm_text") != "DELETE":
        flash("Type DELETE exactly to confirm. No reports were deleted.", "error")
        return _settings_redirect("danger")
    db.delete_all_reports()
    flash("All reports have been deleted.", "success")
    return _settings_redirect("danger")


@app.route("/admin/settings/danger/reset-computers", methods=["POST"])
@login_required
def danger_reset_computers():
    if request.form.get("confirm_text") != "DELETE":
        flash("Type DELETE exactly to confirm. No computers were deleted.", "error")
        return _settings_redirect("danger")
    for c in db.get_all_computers():
        if c["qr_path"]:
            full_path = os.path.join(qr_utils.BASE_DIR, "static", c["qr_path"])
            if os.path.exists(full_path):
                os.remove(full_path)
    db.reset_all_computers()
    flash("All computer records (and their reports) have been deleted.", "success")
    return _settings_redirect("danger")


@app.route("/admin/settings/danger/reset-all", methods=["POST"])
@login_required
def danger_reset_all():
    if request.form.get("confirm_text") != "DELETE":
        flash("Type DELETE exactly to confirm. Nothing was reset.", "error")
        return _settings_redirect("danger")
    if os.path.isdir(qr_utils.QR_FOLDER):
        shutil.rmtree(qr_utils.QR_FOLDER)
        os.makedirs(qr_utils.QR_FOLDER, exist_ok=True)
    db.reset_application_data()
    flash("Application data has been reset. Your admin account and settings were kept.", "success")
    return _settings_redirect("danger")


# ---------------------------------------------------------------------------
# ERROR HANDLERS
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


@app.errorhandler(429)
def too_many_requests(e):
    return render_template("429.html"), 429


@app.errorhandler(500)
def server_error(e):
    return render_template("500.html"), 500


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
