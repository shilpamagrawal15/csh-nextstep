"""NextStep web server: serves the page and a small JSON API backed by Postgres.

Env:
  DATABASE_URL  Postgres connection string (required)
  SECRET_KEY    Flask session signing key (random per-process if unset)

Run locally:  DATABASE_URL=... python3 -m flask --app server run
On Render:    gunicorn server:app
"""
import os
import re
import secrets
from contextlib import contextmanager
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

import bcrypt
import psycopg
from flask import Flask, jsonify, request, send_file, session
from psycopg.rows import dict_row

BASE_DIR = Path(__file__).resolve().parent
PAGE_FILE = BASE_DIR / "Index.html.html"  # the student's page; name kept as-is

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_NAME="nextstep_session",  # own name so other local Flask apps don't clash
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),  # https on Render
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------- database
@contextmanager
def db():
    """One short-lived connection per request; commits on success."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        yield conn


def err(msg, code=400):
    return jsonify({"error": msg}), code


def current_user_id():
    return session.get("user_id")


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user_id():
            return err("Please sign in first.", 401)
        return fn(*a, **kw)

    return wrapper


def user_json(row):
    return {
        "id": str(row["id"]),
        "name": row["full_name"],
        "email": row["email"],
        "role": row["role"],
        "gpa": float(row["unweighted_gpa"]) if row["unweighted_gpa"] is not None else None,
        "budget": row["annual_budget"],
        "points": row["points_balance"] or 0,
    }


def fetch_user(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=%s", (uid,)).fetchone()


def add_points(conn, uid, delta):
    row = conn.execute(
        "UPDATE users SET points_balance = COALESCE(points_balance,0) + %s WHERE id=%s RETURNING points_balance",
        (delta, uid),
    ).fetchone()
    return row["points_balance"]


def get_points(conn, uid):
    return conn.execute("SELECT points_balance FROM users WHERE id=%s", (uid,)).fetchone()["points_balance"]


# ---------------------------------------------------------------- page
@app.get("/")
def index():
    return send_file(PAGE_FILE, mimetype="text/html")


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------------------------------------------------------------- auth
@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE lower(email)=%s", (email,)).fetchone()
    try:
        ok = bool(row) and bcrypt.checkpw(password.encode(), row["password_hash"].encode())
    except ValueError:
        ok = False
    if not ok:
        return err("Incorrect email or password.", 401)
    session.clear()
    session.permanent = True
    session["user_id"] = str(row["id"])
    return jsonify(user_json(row))


@app.post("/api/signup")
def signup():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not name or not EMAIL_RE.match(email):
        return err("Please enter your name and a valid email.")
    if len(password) < 6:
        return err("Password must be at least 6 characters.")
    try:
        gpa = float(data.get("gpa")) if data.get("gpa") not in (None, "") else None
        budget = int(float(data.get("budget"))) if data.get("budget") not in (None, "") else None
    except (TypeError, ValueError):
        return err("GPA and budget must be numbers.")
    if gpa is not None and not 0 <= gpa <= 4:
        return err("GPA must be between 0 and 4.0.")
    if budget is not None and budget < 0:
        return err("Budget cannot be negative.")
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE lower(email)=%s", (email,)).fetchone():
            return err("An account with that email already exists. Please sign in.", 409)
        row = conn.execute(
            """INSERT INTO users (full_name, email, password_hash, role, unweighted_gpa, annual_budget)
               VALUES (%s,%s,%s,'student',%s,%s) RETURNING *""",
            (name, email, pw_hash, gpa, budget),
        ).fetchone()
    session.clear()
    session.permanent = True
    session["user_id"] = str(row["id"])
    return jsonify(user_json(row)), 201


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    uid = current_user_id()
    if not uid:
        return jsonify({"user": None})
    with db() as conn:
        row = fetch_user(conn, uid)
    if not row:
        session.clear()
        return jsonify({"user": None})
    return jsonify({"user": user_json(row)})


@app.put("/api/me")
@login_required
def update_me():
    """Edit Student Profile modal: name, GPA, budget."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    try:
        gpa = float(data.get("gpa"))
        budget = int(float(data.get("budget")))
    except (TypeError, ValueError):
        return err("GPA and budget must be numbers.")
    if not name or not 0 <= gpa <= 4 or budget < 0:
        return err("Please enter a name, a GPA between 0 and 4.0, and a non-negative budget.")
    with db() as conn:
        row = conn.execute(
            "UPDATE users SET full_name=%s, unweighted_gpa=%s, annual_budget=%s WHERE id=%s RETURNING *",
            (name, gpa, budget, current_user_id()),
        ).fetchone()
    return jsonify(user_json(row))


# ---------------------------------------------------------------- checklist
@app.get("/api/checklist")
def checklist():
    uid = current_user_id()
    with db() as conn:
        rows = conn.execute(
            """SELECT c.id, c.title, c.tag, c.point_reward, COALESCE(p.is_completed, false) AS done
               FROM checklist_items c
               LEFT JOIN user_checklist_progress p ON p.checklist_item_id=c.id AND p.user_id=%s
               ORDER BY c.id""",
            (uid,),
        ).fetchall()
    return jsonify([
        {"id": r["id"], "title": r["title"], "tag": r["tag"], "points": r["point_reward"] or 0, "done": r["done"]}
        for r in rows
    ])


@app.put("/api/checklist/<int:item_id>")
@login_required
def set_checklist(item_id):
    """Mark a step done/undone. Like the original page, a step earns its points
    each time it goes from not-done to done."""
    done = bool((request.get_json(silent=True) or {}).get("done"))
    uid = current_user_id()
    with db() as conn:
        item = conn.execute("SELECT point_reward FROM checklist_items WHERE id=%s", (item_id,)).fetchone()
        if not item:
            return err("No such checklist item.", 404)
        prev = conn.execute(
            "SELECT is_completed FROM user_checklist_progress WHERE user_id=%s AND checklist_item_id=%s FOR UPDATE",
            (uid, item_id),
        ).fetchone()
        was_done = bool(prev and prev["is_completed"])
        conn.execute(
            """INSERT INTO user_checklist_progress (user_id, checklist_item_id, is_completed, completed_at)
               VALUES (%s,%s,%s, CASE WHEN %s THEN CURRENT_TIMESTAMP END)
               ON CONFLICT (user_id, checklist_item_id) DO UPDATE
               SET is_completed=EXCLUDED.is_completed, completed_at=EXCLUDED.completed_at""",
            (uid, item_id, done, done),
        )
        points = add_points(conn, uid, item["point_reward"] or 0) if done and not was_done else get_points(conn, uid)
    return jsonify({"id": item_id, "done": done, "points": points})


@app.post("/api/checklist/reset")
@login_required
def reset_checklist():
    with db() as conn:
        conn.execute(
            "UPDATE user_checklist_progress SET is_completed=false, completed_at=NULL WHERE user_id=%s",
            (current_user_id(),),
        )
    return jsonify({"ok": True})


# ---------------------------------------------------------------- scholarships
def scholarship_json(r):
    return {
        "id": r["slug"] or str(r["id"]),
        "title": r["title"],
        "provider": r["provider"],
        "amount": r["amount"],
        "deadline": r["deadline"].isoformat(),
        "minGpa": float(r["min_gpa"]) if r["min_gpa"] is not None else None,
        "major": r["major"],
        "desc": r["description"],
        "isCustom": bool(r["is_custom"]),
    }


def find_scholarship(conn, key):
    """Look up by slug ('sch-1') or by uuid (custom ones without a slug)."""
    return conn.execute(
        "SELECT * FROM scholarships WHERE slug=%s OR id::text=%s", (key, key)
    ).fetchone()


@app.get("/api/scholarships")
def scholarships():
    """Shared scholarships plus the signed-in user's own custom ones (newest first),
    and the list of ids the user has saved."""
    uid = current_user_id()
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM scholarships
               WHERE NOT COALESCE(is_custom,false) OR created_by_user_id=%s
               ORDER BY COALESCE(is_custom,false) DESC, created_at DESC, slug""",
            (uid,),
        ).fetchall()
        saved = []
        if uid:
            saved = [
                r["key"] for r in conn.execute(
                    """SELECT COALESCE(s.slug, s.id::text) AS key FROM user_saved_scholarships us
                       JOIN scholarships s ON s.id=us.scholarship_id WHERE us.user_id=%s ORDER BY us.saved_at""",
                    (uid,),
                )
            ]
    return jsonify({"scholarships": [scholarship_json(r) for r in rows], "saved": saved})


@app.post("/api/scholarships")
@login_required
def add_scholarship():
    """Add Custom Scholarship modal (+50 points). Visible only to its creator."""
    d = request.get_json(silent=True) or {}
    title = (d.get("title") or "").strip()
    provider = (d.get("provider") or "").strip()
    desc = (d.get("desc") or "").strip()
    try:
        amount = int(float(d.get("amount")))
    except (TypeError, ValueError):
        return err("Amount must be a number.")
    try:
        deadline = date.fromisoformat((d.get("deadline") or "").strip())
    except ValueError:
        return err("Deadline must be a date (YYYY-MM-DD).")
    if not (title and provider and desc) or amount <= 0:
        return err("Please fill in every field with a positive amount.")
    uid = current_user_id()
    with db() as conn:
        row = conn.execute(
            """INSERT INTO scholarships (title, provider, amount, deadline, description, is_custom, created_by_user_id)
               VALUES (%s,%s,%s,%s,%s,true,%s) RETURNING *""",
            (title, provider, amount, deadline, desc, uid),
        ).fetchone()
        points = add_points(conn, uid, 50)
    return jsonify({"scholarship": scholarship_json(row), "points": points}), 201


@app.delete("/api/scholarships/<key>")
@login_required
def delete_scholarship(key):
    """Delete one of your own custom scholarships (no UI button; used for cleanup)."""
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM scholarships WHERE (slug=%s OR id::text=%s) AND is_custom AND created_by_user_id=%s",
            (key, key, current_user_id()),
        )
    if cur.rowcount == 0:
        return err("Not found.", 404)
    return jsonify({"ok": True})


@app.post("/api/scholarships/<key>/save")
@login_required
def save_scholarship(key):
    """Bookmark a scholarship (+50 points when newly saved, like the original page)."""
    uid = current_user_id()
    with db() as conn:
        sch = find_scholarship(conn, key)
        if not sch:
            return err("No such scholarship.", 404)
        cur = conn.execute(
            "INSERT INTO user_saved_scholarships (user_id, scholarship_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (uid, sch["id"]),
        )
        points = add_points(conn, uid, 50) if cur.rowcount else get_points(conn, uid)
    return jsonify({"saved": True, "points": points})


@app.delete("/api/scholarships/<key>/save")
@login_required
def unsave_scholarship(key):
    uid = current_user_id()
    with db() as conn:
        sch = find_scholarship(conn, key)
        if not sch:
            return err("No such scholarship.", 404)
        conn.execute(
            "DELETE FROM user_saved_scholarships WHERE user_id=%s AND scholarship_id=%s", (uid, sch["id"])
        )
        points = get_points(conn, uid)
    return jsonify({"saved": False, "points": points})


# ---------------------------------------------------------------- rewards
@app.get("/api/rewards")
def rewards():
    with db() as conn:
        rows = conn.execute("SELECT * FROM rewards_shop ORDER BY cost_points, slug").fetchall()
    return jsonify([
        {"id": r["slug"] or str(r["id"]), "title": r["title"], "cost": r["cost_points"], "category": r["category"],
         "icon": r["icon_name"], "desc": r["description"], "stock": r["stock_quantity"]}
        for r in rows
    ])


@app.post("/api/redemptions")
@login_required
def redeem():
    """Redeem a reward: checks balance, deducts points, records the order."""
    d = request.get_json(silent=True) or {}
    address = (d.get("address") or "").strip()
    option = (d.get("option") or "").strip() or None
    if not address:
        return err("Please enter a shipping address or delivery email.")
    uid = current_user_id()
    with db() as conn:
        reward = conn.execute(
            "SELECT * FROM rewards_shop WHERE slug=%s OR id::text=%s", (d.get("reward_id"), d.get("reward_id"))
        ).fetchone()
        if not reward:
            return err("No such reward.", 404)
        if reward["stock_quantity"] is not None and reward["stock_quantity"] <= 0:
            return err("Sorry, that reward is out of stock.", 409)
        updated = conn.execute(
            """UPDATE users SET points_balance = points_balance - %s
               WHERE id=%s AND points_balance >= %s RETURNING points_balance""",
            (reward["cost_points"], uid, reward["cost_points"]),
        ).fetchone()
        if not updated:
            return err("Not enough points for this reward.", 409)
        red = conn.execute(
            """INSERT INTO redemptions (user_id, reward_id, shipping_address_or_email, size_or_option)
               VALUES (%s,%s,%s,%s) RETURNING id, status""",
            (uid, reward["id"], address, option),
        ).fetchone()
        conn.execute(
            "UPDATE rewards_shop SET stock_quantity = stock_quantity - 1 WHERE id=%s AND stock_quantity IS NOT NULL",
            (reward["id"],),
        )
    return jsonify({"id": str(red["id"]), "status": red["status"], "points": updated["points_balance"]}), 201


# ---------------------------------------------------------------- tutors & mentors
def person_json(r):
    return {
        "id": r["slug"] or str(r["id"]),
        "name": r["display_name"] or r["full_name"],
        "school": r["university"],
        "major": r["major"],
        "testScore": r["test_scores"],
        "rate": r["rate"],
        "status": r["status"],
        "bio": r["bio"],
        "avatar": r["avatar_url"],
        "specialties": r["specialties"] or [],
    }


def people(kind):
    with db() as conn:
        rows = conn.execute(
            """SELECT m.*, u.full_name FROM mentors_and_tutors m LEFT JOIN users u ON u.id=m.user_id
               WHERE m.kind=%s AND m.status <> 'pending_verification' ORDER BY m.slug NULLS LAST, m.created_at""",
            (kind,),
        ).fetchall()
    return jsonify([person_json(r) for r in rows])


@app.get("/api/tutors")
def tutors():
    return people("tutor")


@app.get("/api/mentors")
def mentors():
    return people("mentor")


@app.post("/api/mentor-requests")
@login_required
def mentor_request():
    """Request Tutor Session modal (+100 points)."""
    d = request.get_json(silent=True) or {}
    req_type = d.get("type") or "act_sat_tutoring"
    if req_type not in ("act_sat_tutoring", "mentorship"):
        return err("Unknown request type.")
    notes = (d.get("notes") or "").strip()
    uid = current_user_id()
    with db() as conn:
        m = conn.execute(
            "SELECT id FROM mentors_and_tutors WHERE slug=%s OR id::text=%s", (d.get("mentor_id"), d.get("mentor_id"))
        ).fetchone()
        if not m:
            return err("No such tutor or mentor.", 404)
        row = conn.execute(
            """INSERT INTO mentor_requests (student_id, mentor_id, request_type, notes)
               VALUES (%s,%s,%s,%s) RETURNING id, status""",
            (uid, m["id"], req_type, notes),
        ).fetchone()
        points = add_points(conn, uid, 100) if req_type == "act_sat_tutoring" else get_points(conn, uid)
    return jsonify({"id": str(row["id"]), "status": row["status"], "points": points}), 201


@app.post("/api/mentor-applications")
def mentor_application():
    """'Apply as Mentor/Tutor' form (no login needed). Stored as a pending
    mentors_and_tutors row; it is hidden from the site until staff verify it."""
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    university = (d.get("university") or "").strip()
    major = (d.get("major") or "").strip()
    if not (name and university and major):
        return err("Please fill in every field.")
    with db() as conn:
        row = conn.execute(
            """INSERT INTO mentors_and_tutors (kind, display_name, university, major, bio, status)
               VALUES ('mentor', %s, %s, %s, '', 'pending_verification') RETURNING id""",
            (name[:200], university[:200], major[:200]),
        ).fetchone()
    return jsonify({"id": str(row["id"]), "status": "pending_verification"}), 201


# ---------------------------------------------------------------- colleges & internships
@app.get("/api/colleges")
def colleges():
    with db() as conn:
        rows = conn.execute("SELECT * FROM colleges ORDER BY slug").fetchall()
    return jsonify([
        {"id": r["slug"], "name": r["name"], "type": r["type"], "location": r["location"],
         "minGpa": float(r["min_gpa"]), "annualTuition": r["annual_tuition"], "acceptRate": r["accept_rate"],
         "satRange": r["sat_range"], "image": r["image_url"], "majors": r["majors"] or [], "desc": r["description"]}
        for r in rows
    ])


@app.get("/api/internships")
def internships():
    with db() as conn:
        rows = conn.execute("SELECT * FROM internships ORDER BY slug").fetchall()
    return jsonify([
        {"id": r["slug"], "title": r["title"], "org": r["org"], "target": r["target"], "pay": r["pay"],
         "desc": r["description"]}
        for r in rows
    ])


if __name__ == "__main__":
    app.run(debug=True)
