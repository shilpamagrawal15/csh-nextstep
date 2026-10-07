"""Idempotent seed for NextStep.

Adds the extra columns/tables the web page needs and loads the data that used
to be hard-coded in Index.html.html (scholarships, rewards, tutors, mentors,
colleges, internships). Safe to run any number of times: every row is keyed by
a text `slug` (the page's old ids like 'sch-1') and upserted.

Also replaces the placeholder password hashes of the seed users with a real
bcrypt hash of the demo password `demo1234` (only rows whose hash is not a
valid bcrypt hash are touched).

Usage:  DATABASE_URL=postgresql://... python3 seed.py
"""
import os
import re

import bcrypt
import psycopg

DEMO_PASSWORD = "demo1234"
BCRYPT_RE = re.compile(r"^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")

DDL = """
-- Text slugs so the page can keep its ids ('sch-1', 'rew-1', 'tut-1', ...)
ALTER TABLE scholarships ADD COLUMN IF NOT EXISTS slug varchar UNIQUE;
ALTER TABLE scholarships ADD COLUMN IF NOT EXISTS major varchar;
ALTER TABLE rewards_shop ADD COLUMN IF NOT EXISTS slug varchar UNIQUE;
ALTER TABLE mentors_and_tutors ADD COLUMN IF NOT EXISTS slug varchar UNIQUE;
-- 'tutor' (ACT/SAT Prep tab) or 'mentor' (Connect Mentors tab)
ALTER TABLE mentors_and_tutors ADD COLUMN IF NOT EXISTS kind varchar NOT NULL DEFAULT 'mentor';
-- Name shown on the card (some tutors/mentors have no login account)
ALTER TABLE mentors_and_tutors ADD COLUMN IF NOT EXISTS display_name varchar;
ALTER TABLE mentors_and_tutors ADD COLUMN IF NOT EXISTS rate varchar;
ALTER TABLE mentors_and_tutors ADD COLUMN IF NOT EXISTS specialties text[] NOT NULL DEFAULT '{}';
DO $$ BEGIN
  ALTER TABLE mentors_and_tutors ADD CONSTRAINT mentors_and_tutors_kind_check
    CHECK (kind IN ('mentor', 'tutor'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS colleges (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  slug varchar UNIQUE NOT NULL,
  name varchar NOT NULL,
  type varchar NOT NULL CHECK (type IN ('Public', 'Private')),
  location varchar NOT NULL,
  min_gpa numeric NOT NULL DEFAULT 0.0,
  annual_tuition integer NOT NULL CHECK (annual_tuition >= 0),
  accept_rate varchar,
  sat_range varchar,
  image_url text,
  majors text[] NOT NULL DEFAULT '{}',
  description text NOT NULL,
  created_at timestamptz DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS internships (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  slug varchar UNIQUE NOT NULL,
  title varchar NOT NULL,
  org varchar NOT NULL,
  target varchar NOT NULL CHECK (target IN ('High School', 'College')),
  pay varchar,
  description text NOT NULL,
  created_at timestamptz DEFAULT CURRENT_TIMESTAMP
);
"""

SCHOLARSHIPS = [
    ("sch-1", "Future STEM Leaders Award", "National Science Tech Foundation", 10000, "2026-11-15", 3.2, "STEM Fields",
     "Annual grant for high school seniors and undergrads pursuing computer science or engineering."),
    ("sch-2", "First-Gen Trailblazer Grant", "Opportunity Forward Fund", 5000, "2026-12-01", 2.7, "Any Major",
     "Created for first-generation college applicants demonstrating strong leadership."),
    ("sch-3", "New York TAP Enhancement Incentive", "NYS HESC Program", 4000, "2027-01-30", 2.5, "Any Major",
     "State-level grant for qualified New York state residents staying in-state."),
]

REWARDS = [
    ("rew-1", "$5 Amazon Gift Card", 300, "Gift Card", "credit-card", "Instant digital code for school supplies, books, or electronics."),
    ("rew-2", "$5 Target Gift Card", 300, "Gift Card", "shopping-cart", "Digital code delivered directly to your email."),
    ("rew-3", "$5 Starbucks Gift Card", 300, "Gift Card", "coffee", "Grab a coffee before your next study session or test prep practice."),
    ("rew-4", "Official College Pennant", 500, "Merch", "flag", "Custom felt pennant for Columbia, NYU, SUNY, or CUNY."),
    ("rew-5", "College Logo T-Shirt", 800, "Merch", "shirt", "100% cotton official university t-shirt of your choice."),
    ("rew-6", "University Hoodie / Sweatshirt", 1200, "Merch", "package", "Heavyweight cozy college hoodie sent directly to your home."),
]

# (slug, kind, user email or None, display_name, university, major, test_scores, rate, status, bio, avatar, specialties)
PEOPLE = [
    ("tut-1", "tutor", "david.kim@cornell.edu", "David Kim", "Cornell University", "ACT/SAT Tutor",
     "ACT: 36 (Perfect Math/Science)", "Free / Volunteer", "available",
     "Scored a 36 on the ACT. Specializes in timing strategy for Science and advanced Math shortcuts.",
     "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&q=80&w=200",
     ["ACT Math & Science", "SAT Math"]),
    ("tut-2", "tutor", "maya.p@columbia.edu", "Maya Patel", "Columbia University", "ACT/SAT Tutor",
     "SAT: 1570 (790 Reading/Writing)", "Free / Volunteer", "available",
     "Raised my SAT score from 1310 to 1570. Expert in grammar rules and reading elimination techniques.",
     "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&q=80&w=200",
     ["SAT Reading & Writing", "ACT English"]),
    ("tut-3", "tutor", None, "Ethan Zhang", "NYU Stern Alumni", "ACT/SAT Tutor",
     "ACT: 35 / SAT: 1540", "Volunteer / Mentorship", "available",
     "Helped 15+ students score 32+ on ACT. Focuses on error analysis and pacing logs.",
     "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?auto=format&fit=crop&q=80&w=200",
     ["Overall Pacing", "Test Anxiety"]),
    ("men-1", "mentor", "sarah.chen@columbia.edu", "Sarah Chen", "Columbia University", "Computer Science",
     None, None, "available",
     "First-gen student who navigated FAFSA & full-ride scholarships.",
     "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&q=80&w=200", []),
    # Not linked to the student account 'Marcus Vance' (marcus.v@example.com): different person.
    ("men-2", "mentor", None, "Marcus Vance", "NYU Stern", "Finance & Econ",
     None, None, "busy",
     "Specialized in financial planning and scholarship essays.",
     "https://images.unsplash.com/photo-1539571696357-5a69c17a67c6?auto=format&fit=crop&q=80&w=200", []),
]

COLLEGES = [
    ("col-1", "State University - Main Campus", "Public", "New York, NY", 2.8, 10500, "68%", "1050-1250",
     "https://images.unsplash.com/photo-1541339907198-e08756dedf3f?auto=format&fit=crop&q=80&w=600",
     ["Computer Science", "Business", "Nursing"], "Top-tier public research university with excellent STEM programs."),
    ("col-2", "Metropolitan Tech Institute", "Public", "Buffalo, NY", 3.2, 8900, "54%", "1180-1380",
     "https://images.unsplash.com/photo-1562774053-701939374585?auto=format&fit=crop&q=80&w=600",
     ["Engineering", "Data Science"], "Premier urban technology-focused university with co-op placement."),
    ("col-3", "Empire Liberal Arts College", "Private", "Saratoga Springs, NY", 3.5, 38000, "35%", "1280-1450",
     "https://images.unsplash.com/photo-1523050854058-8df90110c9f1?auto=format&fit=crop&q=80&w=600",
     ["Biology", "Political Science"], "Prestigious private liberal arts college offering 100% need-met aid."),
]

INTERNSHIPS = [
    ("int-1", "Summer Youth Tech Fellowship", "Empire Tech Alliance", "High School", "Paid ($2,000)",
     "8-week software development program pairing students with mentors."),
    ("int-2", "Healthcare & Life Sciences Internship", "Metropolitan Health", "High School", "Paid ($18/hr)",
     "Shadowing in biomedical research labs for aspiring pre-med students."),
    ("int-3", "Data Analytics Undergraduate Co-Op", "FinTech Solutions", "College", "Paid ($25/hr)",
     "Full summer position for undergrads studying computer science or math."),
]

# The page used to start every visitor with checklist steps 1-2 done and
# scholarships sch-1 + sch-3 saved; give the main demo student the same start.
DEMO_STUDENT = "alex.rivera@example.com"
DEMO_DONE_ITEMS = [1, 2]
DEMO_SAVED = ["sch-1", "sch-3"]


def main():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(DDL)

        for s in SCHOLARSHIPS:
            cur.execute(
                """INSERT INTO scholarships (slug, title, provider, amount, deadline, min_gpa, major, description, is_custom)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false)
                   ON CONFLICT (slug) DO UPDATE SET title=EXCLUDED.title, provider=EXCLUDED.provider,
                     amount=EXCLUDED.amount, deadline=EXCLUDED.deadline, min_gpa=EXCLUDED.min_gpa,
                     major=EXCLUDED.major, description=EXCLUDED.description""", s)

        for r in REWARDS:
            cur.execute(
                """INSERT INTO rewards_shop (slug, title, cost_points, category, icon_name, description)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (slug) DO UPDATE SET title=EXCLUDED.title, cost_points=EXCLUDED.cost_points,
                     category=EXCLUDED.category, icon_name=EXCLUDED.icon_name, description=EXCLUDED.description""", r)

        for (slug, kind, email, name, uni, major, scores, rate, status, bio, avatar, spec) in PEOPLE:
            cur.execute(
                """INSERT INTO mentors_and_tutors (slug, kind, user_id, display_name, university, major, test_scores,
                                                  rate, status, bio, avatar_url, specialties)
                   VALUES (%s,%s,(SELECT id FROM users WHERE email=%s),%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (slug) DO UPDATE SET kind=EXCLUDED.kind, user_id=EXCLUDED.user_id,
                     display_name=EXCLUDED.display_name, university=EXCLUDED.university, major=EXCLUDED.major,
                     test_scores=EXCLUDED.test_scores, rate=EXCLUDED.rate, status=EXCLUDED.status, bio=EXCLUDED.bio,
                     avatar_url=EXCLUDED.avatar_url, specialties=EXCLUDED.specialties""",
                (slug, kind, email, name, uni, major, scores, rate, status, bio, avatar, spec))

        for c in COLLEGES:
            cur.execute(
                """INSERT INTO colleges (slug, name, type, location, min_gpa, annual_tuition, accept_rate, sat_range,
                                        image_url, majors, description)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name, type=EXCLUDED.type, location=EXCLUDED.location,
                     min_gpa=EXCLUDED.min_gpa, annual_tuition=EXCLUDED.annual_tuition, accept_rate=EXCLUDED.accept_rate,
                     sat_range=EXCLUDED.sat_range, image_url=EXCLUDED.image_url, majors=EXCLUDED.majors,
                     description=EXCLUDED.description""", c)

        for i in INTERNSHIPS:
            cur.execute(
                """INSERT INTO internships (slug, title, org, target, pay, description) VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (slug) DO UPDATE SET title=EXCLUDED.title, org=EXCLUDED.org, target=EXCLUDED.target,
                     pay=EXCLUDED.pay, description=EXCLUDED.description""", i)

        # Real bcrypt hash for every seed user still carrying the fake placeholder.
        demo_hash = bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt()).decode()
        cur.execute("SELECT id, password_hash FROM users")
        for uid, h in cur.fetchall():
            if not BCRYPT_RE.match(h or ""):
                cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (demo_hash, uid))

        # Demo student's starting progress (only inserted if missing).
        for item_id in DEMO_DONE_ITEMS:
            cur.execute(
                """INSERT INTO user_checklist_progress (user_id, checklist_item_id, is_completed, completed_at)
                   SELECT id, %s, true, CURRENT_TIMESTAMP FROM users WHERE email=%s
                   ON CONFLICT (user_id, checklist_item_id) DO NOTHING""", (item_id, DEMO_STUDENT))
        for slug in DEMO_SAVED:
            cur.execute(
                """INSERT INTO user_saved_scholarships (user_id, scholarship_id)
                   SELECT u.id, s.id FROM users u, scholarships s WHERE u.email=%s AND s.slug=%s
                   ON CONFLICT DO NOTHING""", (DEMO_STUDENT, slug))

        conn.commit()
        for t in ["users", "checklist_items", "scholarships", "rewards_shop", "mentors_and_tutors",
                  "colleges", "internships", "user_checklist_progress", "user_saved_scholarships"]:
            cur.execute(f"SELECT count(*) FROM {t}")
            print(f"{t}: {cur.fetchone()[0]}")


if __name__ == "__main__":
    main()
