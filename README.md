# SmartLab — QR-Based Computer Laboratory Monitoring System (Flask)

A Flask + SQLite web application that gives every computer in a lab its own
QR code. Scanning it opens a mobile-friendly page showing that computer's
live status, with no login required, and lets anyone report a problem on it.
Admins manage laboratories, computers, statuses, and reports from a
dashboard.

A separate **SmartLab Desktop** companion app (Tkinter) also exists, reusing
this project's `database.py` and `qr_utils.py` unmodified, for admins who
prefer a native desktop window over a browser. See its own README in that
project's folder.

## Features

- Admin login with hashed passwords (Werkzeug)
- Professional dashboard: system health %, status distribution donut chart,
  most-common-issues bar chart, open/resolved report totals
- **Laboratories as real physical locations**: each lab is its own database
  record (name, code, building, floor, room), not just a text label — every
  computer links to one, with its own dashboard showing that lab's live
  computer counts, health, and open reports
- **Dynamic grouping by laboratory**: the Computers, Reports, and Statistics
  pages all group automatically by whatever laboratories currently exist —
  adding a new lab in Settings makes it appear everywhere with no code
  changes
- Computer management: add, filter (All/Working/Under Repair/Offline),
  search, per-computer detail page with QR code and report history
- Public, no-login QR status page per computer, optimized for phones
- Problem reporting with categorized issue types
- Report management with Open/Resolved tabs, priority levels, and one-click
  resolve, plus a **printable report log** grouped by laboratory with
  signature lines for a physical record
- Notification bell showing recent open reports
- **Light/Dark theme** — a genuinely functional toggle in the topbar and in
  Settings → Appearance, with the choice saved per administrator
- Fully responsive: sidebar collapses to a mobile drawer, cards stack,
  tables reflow on small screens
- **Full Settings system** (10 sections): General, Account (username/password),
  Laboratory (add physical laboratories with building/floor/room), Appearance
  (including the Light/Dark theme), QR code size/regeneration/bulk
  download/print, Security (session timeout, activity log), System
  (backup/check/clear old reports), Notifications, About, and a
  confirmation-gated Danger Zone. See "Settings feature" below.
- **Smart "Done" buttons on every form**: submit buttons stay disabled
  (faded, unclickable) until all required fields hold genuinely valid
  data, turn solid SmartLab green the moment the form is actually
  complete, and disable themselves again on submit to prevent double
  submission. See "Form completion behavior" below.
- **In-app QR scanner** (Scan QR in the sidebar): point a webcam or
  phone camera at any computer's printed QR code from inside the admin
  dashboard itself, and it jumps straight to that computer's page — no
  typing or searching needed. Falls back to a manual ID lookup if no
  camera is available. See "QR scanner" below.

## Requirements

- Python 3.9+

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then edit .env and set a real SMARTLAB_SECRET_KEY
python app.py
```

Visit `http://127.0.0.1:5000`. Log in at `/login` with:
- Username: `admin`
- Password: `admin123`

**Change this password immediately** via Settings after your first login.

The database (`instance/smartlab.db`) and QR code images
(`static/qrcodes/`) are created automatically on first run.

## Project structure

```
smartlab_web/
├── app.py                 # Flask routes
├── database.py             # SQLite setup and all queries
├── qr_utils.py              # QR code generation
├── requirements.txt
├── .env.example
├── templates/                # Jinja2 HTML templates
│   ├── base_admin.html         # sidebar + topbar shell for admin pages
│   ├── landing.html, login.html
│   ├── dashboard.html, computers.html, computer_detail.html, add_computer.html
│   ├── reports.html, statistics.html, settings.html
│   ├── public_status.html, report_confirmation.html
│   └── _icons.html, _badges.html   # shared Jinja macros
├── static/
│   ├── css/style.css         # design system (single stylesheet)
│   ├── js/main.js             # mobile nav + notification dropdown
│   └── qrcodes/                # generated QR images (created at runtime)
└── instance/
    └── smartlab.db            # created at runtime, not tracked in git
```

## How to test the full defense workflow

1. Log in as admin → **Computers → Add computer**. Fill in an ID
   (e.g. `LABA-PC01`), a lab name, and a workstation number. Save.
2. A QR code is generated automatically. Click **View QR** to see it, or
   open it on your phone's camera — it opens the public status page directly.
3. From that public page (no login), select an issue type and submit a report.
4. Back in the admin, the **Reports** page and the notification bell now show
   the new report, and the computer's status has flipped to "Under Repair".
5. Click **Resolve** on the report — the computer's status returns to
   "Working" automatically (as long as it has no other open reports).
6. Check the **Dashboard** and **Statistics** pages — the counts and charts
   update immediately to reflect the change.

## Settings feature

Visit **Settings** in the sidebar after logging in. Ten tabs on the left:

- **General** — system name/description, admin email, default lab, and an
  Allow Anonymous Reports toggle that genuinely changes public-form behavior.
- **Account** — profile, change username, change password (all require your
  current password).
- **Laboratory** — add/remove reference info (name, location, description)
  for each lab. This is separate from the free-text lab field on computers,
  so it never touches existing computer records.
- **Appearance** — theme/accent/compact/animations preferences, saved to
  the database (applies to the admin interface only; public QR pages stay
  simple and consistent for everyone).
- **QR Codes** — choose a size, then Regenerate All, Download All (.zip), or
  open a Print-friendly page. Every QR code still encodes the same public
  URL — only its physical size changes.
- **Security** — session timeout + auto-logout (really applied via Flask's
  session lifetime), plus a real login/logout/failed-login activity log.
- **System** — live database/QR-storage health checks, one-click SQLite
  backup download, a confirmation-gated "clear resolved reports older than
  90 days," and CSV export for computers and reports.
- **Notifications** — toggle what the bell badge/flash messages show.
- **About** — editable project/team info for your defense.
- **Danger Zone** — Delete All Reports / Reset Computers / Reset All Data.
  Each requires typing `DELETE` exactly into a text field (checked
  server-side, not just a JS popup) before anything is deleted. Your admin
  account and saved settings are never touched by any of these, so you can
  never lock yourself out.

## Form completion behavior

Every form that requires real input (Add Computer, Report a Problem, Change
Username/Password, Add Laboratory, Change Status, Danger Zone, and more)
uses a shared script, `static/js/forms.js`, built on the browser's own
HTML5 validation rather than a custom framework:

- Add `data-smartlab-validate` to a `<form>` and `data-done-btn` to its
  submit button, and the button automatically stays disabled until every
  field marked `required` (plus `minlength`, `type="email"`, etc.) holds
  valid data. Optional fields never block it.
- Cross-field rules that HTML alone can't express — the password
  confirmation matching, a Danger Zone field reading exactly `DELETE`,
  or a status dropdown actually being changed from its current value —
  are added with a few lines of `input.setCustomValidity(...)`, which
  plugs directly into the same disabled/enabled check.
- On submit, the button disables itself immediately and its label changes
  to a "please wait" message, so a slow connection can't result in the
  same form being submitted twice.
- Forms where nothing can truly be "invalid" (Appearance, QR size,
  Security, Notifications, About — every field already has a valid
  default value) use the lighter `data-smartlab-guard` marker instead:
  no artificial gating, just the duplicate-submission guard.

Required fields are marked with a small red asterisk in their label.

## QR scanner

`/admin/scan` (protected, like every other admin page) opens the device's
camera and decodes QR codes entirely in the browser using
[jsQR](https://github.com/cozmo/jsQR) — bundled locally in
`static/js/jsQR.js` rather than loaded from a CDN, so it keeps working
even without an internet connection (the same approach used for
`chart.min.js`).

- It requests the rear ("environment") camera first, since that's what a
  phone uses to scan a physical sticker, and falls back to any available
  camera (e.g. a laptop's single webcam) if that's not possible.
- Every video frame is drawn to a hidden canvas and decoded with jsQR;
  since a SmartLab QR code always encodes a URL ending in
  `/qr/<computer_id>`, that ID is pulled straight out of the decoded
  text and the admin is sent to `/admin/computers/<id>`.
- Scanning an unrelated QR code (not one SmartLab generated) is detected
  and shown as a message, rather than trying to redirect somewhere
  invalid.
- If the browser has no camera or access is denied, a manual "type the
  ID" fallback below the viewfinder still works, gated by the same
  Done-button pattern described above.

## Security notes

- Passwords are hashed with Werkzeug's `generate_password_hash` /
  `check_password_hash` — never stored or displayed in plain text.
- All SQL queries use parameterized placeholders (`?`), never string
  formatting, to prevent SQL injection.
- The Flask secret key is read from the `SMARTLAB_SECRET_KEY` environment
  variable; a fallback dev key is used only if it's not set, which is
  flagged in `app.py` — set a real one before deploying.
- Debug mode is off by default (`app.run(debug=False, ...)`).
- Admin routes are protected by a `login_required` decorator that checks
  the session; public routes (`/`, `/qr/<id>`, login) intentionally require
  no authentication.
