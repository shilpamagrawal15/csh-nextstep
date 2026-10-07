# NextStep database

The page (`Index.html.html`) is now served by a small Flask server (`server.py`)
that reads and writes a Postgres database. All of the page's lists (checklist,
rewards, scholarships, tutors, mentors, colleges, internships) come from the
database, and everything a signed-in student does is saved there instead of in
the browser's localStorage. JS changes in the page are marked with `// [DB]`.

## Demo login

The sign-in form is prefilled with a working demo account:

- `alex.rivera@example.com` / `demo1234`

Every seed user has the password `demo1234`: students `sam.wu@example.com`,
`marcus.v@example.com`; tutors `david.kim@cornell.edu`, `maya.p@columbia.edu`;
mentor `sarah.chen@columbia.edu`. "Student Signup" creates a new student account.

## Tables

| Table | What it holds |
|---|---|
| `users` | Accounts (bcrypt `password_hash`, role, GPA, budget, `points_balance`) |
| `checklist_items` | FAFSA / TAP / SAT-ACT steps and their point reward |
| `user_checklist_progress` | Which steps each user has checked off |
| `scholarships` | Shared scholarships (`slug` = page id like `sch-1`) and students' custom ones (`is_custom`, visible only to their creator) |
| `user_saved_scholarships` | Bookmarked scholarships per user |
| `rewards_shop` | Gift shop items (`slug` like `rew-1`, cost, Lucide icon name, stock) |
| `redemptions` | Reward orders (address/email, size option, status) |
| `mentors_and_tutors` | Tutors (`kind='tutor'`) and mentors (`kind='mentor'`); applications from the "Apply as Mentor/Tutor" form are stored with `status='pending_verification'` and hidden until approved |
| `mentor_requests` | Tutoring session requests from students |
| `colleges` | College Matcher cards (added) |
| `internships` | Internship cards (added) |

`schema.sql` has the full schema. `seed.py` is idempotent: it adds the extra
columns/tables, upserts the page's original data by slug, sets real bcrypt
hashes for the seed users, and gives Alex the page's old starting state
(steps 1-2 done, `sch-1` and `sch-3` saved). The `users` and `checklist_items`
rows themselves came from the original fake-data file and are not created by `seed.py`.

## API

| Method & path | Login? | Purpose |
|---|---|---|
| `GET /` | | The page |
| `POST /api/login` `{email, password}` | | Sign in |
| `POST /api/signup` `{name, email, password, gpa, budget}` | | Create a student account and sign in |
| `POST /api/logout` | | Sign out |
| `GET /api/me` | | `{user: {...}}` or `{user: null}` |
| `PUT /api/me` `{name, gpa, budget}` | yes | Edit profile |
| `GET /api/checklist` | | Steps, with `done` for the signed-in user |
| `PUT /api/checklist/<id>` `{done}` | yes | Check/uncheck a step (+points when checked) |
| `POST /api/checklist/reset` | yes | "Reset Tasks" |
| `GET /api/scholarships` | | `{scholarships, saved}` |
| `POST /api/scholarships` `{title, provider, amount, deadline, desc}` | yes | Add custom scholarship (+50) |
| `DELETE /api/scholarships/<id>` | yes | Delete your own custom scholarship |
| `POST` / `DELETE /api/scholarships/<id>/save` | yes | Save (+50) / unsave |
| `GET /api/rewards` | | Gift shop items |
| `POST /api/redemptions` `{reward_id, address, option}` | yes | Redeem (server checks and deducts points) |
| `GET /api/tutors`, `GET /api/mentors` | | Approved tutors / mentors |
| `POST /api/mentor-requests` `{mentor_id, type, notes}` | yes | Request a tutoring session (+100) |
| `POST /api/mentor-applications` `{name, university, major}` | | Apply as mentor/tutor (pending) |
| `GET /api/colleges`, `GET /api/internships` | | College and internship cards |

Points are always calculated on the server; the page just shows the balance it
gets back.

## Run locally

```sh
pip install -r requirements.txt
DATABASE_URL=postgresql://... python3 -m flask --app server run
# first time against a fresh database:
#   psql "$DATABASE_URL" -f schema.sql   (empty DB only)
#   DATABASE_URL=postgresql://... python3 seed.py
```

Environment: `DATABASE_URL` (required) and `SECRET_KEY` (session signing key;
a random one is used if unset, which logs everyone out on restart).
On Render the start command is `gunicorn server:app`.
