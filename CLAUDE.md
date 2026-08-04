# Hennings Alternativ Jul — mobile app + backend

Volunteer-facing mobile app (Expo/React Native) and its Django REST API,
in one repo, for a 56-year-old Oslo Christmas charity's shift/oppgave
(task) management. Everything here is used by volunteers and event admins
who've already registered — first-time public signup happens on a separate
website repo, which links to the API this backend exposes.

**Repo name gotcha:** the npm package is named `qr_app` and there is a
separate, stale GitHub repo literally called `qr_app` (`Darzaguhl/qr_app`,
last touched at migration 0009) — that one is abandoned. **The real,
actively-developed repo is `Darzaguhl/hennings-alternativ-jul-app`.** If you
ever need to clone this project fresh, clone that one, not `qr_app`, or
you'll be looking at nine-migrations-old code and wonder why nothing
matches.

Related repos (not in this checkout):
- Public website (registration form, no login) — separate static-site repo.
- Admin dashboard (React/Vite) — separate repo, reachable via
  `ADMIN_DASHBOARD_URL`; this backend builds invite-email links to it.

## Stack

- **App**: Expo SDK 54 (React Native 0.81, React 19), TypeScript, file-based
  routing via `expo-router`. No web-only bundler config beyond Expo's
  defaults (no custom `metro.config.js`/`babel.config.js`/`eas.json`).
- **Backend**: Django 5.2 + Django REST Framework + `djangorestframework-simplejwt`
  (JWT auth), `django-cors-headers`, SQLite locally / Postgres in production
  via `dj-database-url` + `DATABASE_URL`, Resend for transactional email,
  WhiteNoise for static files, Gunicorn in production.

## Build / run / test commands (verified working)

### App (repo root)

```bash
npm install
npm run start      # expo start — Metro bundler, scan QR or press a platform key
npm run web         # expo start --web
npm run android      # expo start --android
npm run ios          # expo start --ios
npm run lint          # expo lint (ESLint, eslint-config-expo)
npx tsc --noEmit       # typecheck — no dedicated npm script, but this works and is clean
```

There is no app-level automated test suite (no Jest config, no `test`
script) — verification is manual, via the simulator/device or `expo start
--web`.

`npm run lint` currently reports 3 pre-existing problems (1 error in
`components/CheckinSection.tsx`, 2 unused-var warnings in
`app/(tabs)/events/index.tsx` and `app/set-password.tsx`) on a clean
checkout. Don't assume you introduced these — only treat lint output as a
regression if it's new relative to `git diff`.

### Backend (`backend/`)

```bash
cd backend
python3 -m venv venv           # create the venv INSIDE backend/, not the repo root (see Gotchas)
source venv/bin/activate
pip install -r requirements.txt
python3 manage.py migrate
DJANGO_SECRET_KEY=dev python3 manage.py runserver 8000
```

Run the test suite (196 tests, ~100s, all passing on a clean checkout):

```bash
DJANGO_SECRET_KEY=dev python3 manage.py test api --verbosity 2
```

Check for missing/uncommitted migrations before opening a PR that touches
`models.py`:

```bash
DJANGO_SECRET_KEY=dev python3 manage.py makemigrations --check --dry-run
```

`DJANGO_SECRET_KEY` has an insecure local-dev fallback baked into
`settings.py`, so omitting it still works — set it explicitly in CI-like
contexts anyway since that's the pattern the rest of the settings file
uses (env var with a documented-unsafe-for-prod fallback).

## Folder structure

```
app/                     Expo Router screens (file-based routing)
  _layout.tsx             Root layout — wraps everything in AuthProvider/SettingsProvider
  index.tsx                Landing/redirect screen
  login.tsx                 Email+password login
  set-password.tsx           In-app equivalent of the website's set-password page
  AuthContext.tsx             Auth state, token storage, apiFetch() — see Conventions
  SettingsContext.tsx          Local-only display prefs (time/date format), AsyncStorage-backed
  (tabs)/                       Tab navigator
    _layout.tsx
    profile.tsx
    qrcode.tsx                   Shows the volunteer's own check-in QR code
    events/index.tsx              Main event screen: vakter, oppgaver, pool, check-in sections
components/                Section components used by app/(tabs)/events, plus shared ui/
  VakterSection.tsx, OppgaverSection.tsx, PoolSection.tsx, CheckinSection.tsx
  ui/                       Cross-platform primitives (icon symbol, tab bar background)
constants/                Colors/theme tokens
hooks/                    useColorScheme (+ .web variant), useThemeColor
assets/                   Icons, splash image, fonts
backend/
  manage.py
  requirements.txt
  backend/                 Django project package: settings.py, urls.py, wsgi.py, asgi.py
  api/                       The one Django app. Everything lives here:
    models.py                 Skill, User, Event, Membership, Invite,
                                PasswordSetupToken, QRCode, Shift, OppgaveSlot,
                                ShiftSignup, ShiftConflict, X1Signup,
                                EventCheckIn, Assignment
    serializers.py
    views.py                   ViewSets + a handful of function-based views
                                 (public_event, accept_invite, set_password, ...)
    urls.py                    DRF router + explicit paths, see its own comments
                                 for which endpoints are public vs authenticated
    auth_backends.py            EmailBackend — login by email instead of username
    email.py                     Resend integration (invite + password-setup emails)
    throttling.py                 Per-endpoint DRF throttle scopes
    admin.py
    tests.py                      ~2700 lines, one file, Django TestCase/APITestCase classes
    migrations/
```

## Key conventions

- **App→API calls go through `apiFetch` from `useAuth()`** (`app/AuthContext.tsx`),
  not a raw `fetch`. It resolves relative paths against `API_BASE_URL`,
  attaches the `Authorization: Bearer` header, and — on a 401 — transparently
  refreshes the access token and retries the request once before giving up.
  Only `AuthContext.tsx` itself calls the global `fetch` directly; every
  screen/component below it uses `apiFetch`.
- **Token storage is platform-split**: native (iOS/Android) uses
  `expo-secure-store` (Keychain/Keystore-backed); web (`expo start --web`,
  used for local testing) falls back to `AsyncStorage`, since there's no
  secure-enclave equivalent in a browser. Both are wrapped behind the same
  `tokenStorage` interface in `AuthContext.tsx` — don't call
  `SecureStore`/`AsyncStorage` directly from elsewhere for tokens.
- **JWT refresh tokens rotate and blacklist on use** (`SIMPLE_JWT` in
  `settings.py`: 1h access / 14d refresh, `ROTATE_REFRESH_TOKENS` +
  `BLACKLIST_AFTER_ROTATION`). Any client-side refresh flow must persist the
  *new* refresh token returned from `/api/token/refresh/`, not just the new
  access token — reusing the old refresh token after rotation fails and
  force-logs the user out. `apiFetch`'s refresh-and-retry logic already does
  this correctly; mirror it if you add another token-refresh call site.
- **Login is by email**, via a custom `EmailBackend` (`auth_backends.py`)
  layered in front of the default `ModelBackend` — the latter is kept only
  so Django admin's username-based login keeps working. `POST /api/token/`
  expects `email`/`password`, not `username`/`password`.
- **`MeSerializer` vs `UserSerializer` privacy split** (in `serializers.py`):
  `UserSerializer` is embedded broadly — shift participants, leaders, pool
  entries, assignments — anywhere another volunteer on the same shift can
  see it. It must never carry personal fields (phone, address, birthdate,
  `about`, `experience_notes`, `participation_years`). Those live only on
  `MeSerializer`, used solely where the viewer is already gated to "your own
  data" or "an admin/staff/roster-viewer" — see
  `UserViewSet.get_serializer_class`. Adding a new personal field means
  adding it to `MeSerializer`, not `UserSerializer`.
- **`ShiftConflict` is admin-curated, not computed from time overlap.**
  Two shifts can legitimately overlap in time and still be a fine
  combination (see the model's docstring) — don't "simplify" conflict logic
  back into a time-range comparison.
- **`X1Signup` eligibility is validated once, at creation** (in
  `X1SignupViewSet.perform_create`: ≥3 distinct signed-up shifts, ≥2 of them
  with `vakt_number` in 5–10) — it is *not* re-validated if the volunteer
  later withdraws one of the underlying shift signups. That's intentional,
  not a bug to "fix."
- **`Event.year_label`** is the one shared way to group history/participation
  by year (falls back to the event's title when it has no `date` set). Reuse
  it — don't reimplement year-grouping logic elsewhere; multiple places
  (`oppgave_history`, `MeSerializer.participation_years`) depend on it
  producing the same grouping.
- **Sensitive unauthenticated endpoints are throttled per-endpoint**
  (`throttling.py` + `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`:
  `register`, `login`, `password_setup_request`, `password_setup_confirm`).
  The rest of the API relies on `DEFAULT_PERMISSION_CLASSES = IsAuthenticated`
  instead and isn't separately throttled. A new public endpoint should
  probably get its own throttle scope, not ride on the defaults.
- **Env vars follow an `or`-not-`.get(default)` pattern for optional
  secrets** (`RESEND_API_KEY`, `RESEND_FROM_EMAIL`) — a platform env var
  that's set-but-blank still satisfies `os.environ.get(key, default)`'s
  "key exists" check and silently wins over the default with `""`. Using
  `os.environ.get(key) or default` treats blank the same as unset. Follow
  this pattern for any new optional secret/URL setting.

## Deploy

Backend and app are deployed separately (not covered by anything in this
repo's tooling) — `ADMIN_DASHBOARD_URL` and `WEBSITE_URL` env vars on the
backend are how it builds cross-repo links (invite emails, password-setup
emails) for whichever environment it's running in.

## Workflow

This repo uses **feature branches + PRs into `main`** (unlike the public
website repo, which pushes directly to `main`) — see the git log for the
pattern (`Merge pull request #NN from Darzaguhl/feature/...`). Branch,
commit, push, open a PR; don't push straight to `main`.

## Gotchas

- **Don't create a Python venv at the repo root.** `.gitignore` has
  `bin/`, `lib/`, `.venv/`, and `pyvenv.cfg` entries at the top level —
  leftovers from someone once running `python3 -m venv .` from the repo
  root instead of from inside `backend/`. Always `cd backend` first.
- **`API_BASE_URL` in the app is a hardcoded constant in
  `app/AuthContext.tsx`**, not an env-driven/build-time-swapped value like
  the website's `js/config.js`. To point the app at a local backend for
  testing, edit that constant directly, test, then **revert it** before
  committing — there's no separate local-vs-prod config file to isolate the
  change.
- **`backend/db.sqlite3` is gitignored** — a fresh clone has no database
  until you run `python3 manage.py migrate`.
- **No CI.** Nothing runs tests/lint/typecheck automatically on push or PR —
  run `manage.py test api`, `npm run lint`, and `npx tsc --noEmit` yourself
  before opening a PR.
- **`api/tests.py` is one ~2700-line file**, not split per model/feature —
  find the right `TestCase` class by name (they're descriptively named,
  e.g. `X1SignupTests`, `RegistrationAndEmailLoginTests`) rather than
  expecting a `tests/` package.
