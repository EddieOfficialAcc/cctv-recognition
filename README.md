# CCTV Recognition

Development starter for the retail CCTV health and visual anomaly inspection
system described in the project requirements. It contains a React, TypeScript,
Vite and Tailwind CSS frontend and a Python FastAPI backend.

## Structure

- `frontend/` - web application.
- `backend/app/` - FastAPI application.
- `backend/tests/` - backend tests.

The screenshot workflow supports validated uploads, persisted recognition
results and evidence, a searchable/paginated inspection table, and operator
review with notes. One vision API request analyzes the screenshot and optional
burst frames and approved baseline. An opt-in scheduled DAHAU capture worker
is present but has not been tested against a recorder and is disabled by default.
The health endpoint checks the API process, not any camera or database.

The authenticated **Camera configuration** tab displays a read-only,
paginated selection of current `dbo.cctv_config` fields for all brands.
Use **Refresh** to reload it; the tab does not poll automatically. It shows
only whether both credentials are present, never the username or password.
The application database login requires SELECT permission on `dbo.cctv_config`.

### Scheduled DAHAU inspections (unverified)

The optional worker selects only `brand = 'DAHAU'` rows with non-NULL shop
name, Internal IP, administrator name and password. It uses port 81, HTTP
Digest authentication and the DAHAU snapshot endpoint. It attempts to read
channel titles through the recorder API, then falls back to channels 1 through
`cam_qty` if discovery fails. Neither the title-to-snapshot channel mapping nor
the discovery endpoint has been verified on the target recorders; both methods
are reported as uncertain and the fallback may miss channels starting above 1.
Reports show the recorder's channel names where available. To keep baseline
matching independent of channel-name or shop-configuration changes, automated
recognition records instead use a stable channel-number and opaque recorder
identifier as the camera name. Existing baselines approved under other names
will not match; approve new baselines for these identities. The identifier
mapping is stored in the application database and must be retained for baseline
continuity. Changing a recorder's address, shop or region creates a new identity.
Duplicate entries for the same shop and recorder are skipped.

The worker starts a run at 09:30 Asia/Hong_Kong; capture is sequential, so
individual capture times may be much later. A missed start is reported rather
than run later. Snapshots are saved before vision analysis; completed records
use the existing three checks and appear in the normal recognition view. The
daily reports show per-channel results and downloadable saved evidence, including
analysis failures. The report lists up to the latest 100 run dates. Interrupted
runs can have an incomplete item list. A worker restart
marks interrupted work and missed runs when the database becomes available.

Do not enable the worker until device behavior, network access, image handling,
permissions, runtime, storage capacity and vision-service costs are verified.
HTTP does not encrypt screenshots; use a trusted network. Basic authentication
is not supported. The container needs network access to the configured Internal
IPs; the separate database `port` field is not used for snapshots. The schema
must be reapplied before use, and the runtime SQL login needs SELECT on
`dbo.cctv_config` and SELECT, INSERT and UPDATE on the inspection tables as
well as the existing recognition tables. Restrict the platform to trusted
operators and ensure camera-image transmission to the vision service is approved.

The Compose `inspection` profile is disabled by default. After verification,
set `SCHEDULED_INSPECTION_ENABLED=true` in the Compose environment and start
the `inspection-worker` with the profile enabled, alongside the normal services.
The worker uses a SQL Server application lock to prevent concurrent workers.
Monitor its process and logs; it does not automatically restart after an error.

## Local development (Windows PowerShell)

Prerequisites: Node.js and npm supported by the Vite version in `frontend/`,
and Python 3.10 or later.

Start the backend from the project root:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -r backend\requirements-dev.txt
Set-Location backend
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

In another terminal, start the frontend:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open the URL printed by Vite (normally http://localhost:5173). Its development
server forwards `/api` requests to FastAPI at http://127.0.0.1:8000. API
documentation is available at http://127.0.0.1:8000/docs.

The entry screen requires an account. Set up SQL Server, the schema, and an
account as described below before signing in. For local HTTP development only,
set `AUTH_COOKIE_SECURE=false` in `backend\.env`. The default is `true` and
requires HTTPS, including in production.

## SQL Server connection

SQL Server on Linux uses the same SQL Server protocol as Windows. Install
Microsoft ODBC Driver 18 for SQL Server on the backend machine (Driver 17 also
works when selected with `DB_DRIVER`).

Copy `backend/.env.example` to `backend/.env` and fill in the SQL Server host,
database, SQL login and password. The local file is ignored by Git and excluded
from the Docker image. Never place credentials in frontend code. Environment
variables override the file; it is loaded independently of the working directory.

```powershell
# From the project root, if backend\.env does not already exist:
Copy-Item backend\.env.example backend\.env

# After filling in backend\.env:
Set-Location backend
.\.venv\Scripts\python -m app.database
```

The check runs only `SELECT DB_NAME()` and verifies the selected database.
`GET /api/health/database` returns `{"status":"ok","database":"cctv_recognition"}`
when connected and HTTP 503 when configuration or connectivity fails.
`GET /api/health` remains an independent API liveness check.

Encryption is always enabled. `DB_TRUST_SERVER_CERTIFICATE=true` can be used
for a development server with a self-signed certificate, matching a database
client's "Trust Server Certificate" option. For production, configure a trusted
certificate and leave this setting `false`. Use a dedicated least-privilege
application login rather than `sa`, and rotate any password shared in chat.

If the server's SQL port is accessible directly, use its address and port in
`DB_HOST` and `DB_PORT`. An SSH tunnel configured in a database client is not
automatically available to the backend. If tunneling is required, run this in
a separate terminal with your SSH account (not the SQL login):

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:11433:localhost:1433 SSH_USER@SSH_HOST
```

Then set `DB_HOST=127.0.0.1` and `DB_PORT=11433` for the locally running backend.
Keep the SSH terminal open; enter its password interactively. Inside Docker,
`127.0.0.1` is the container itself, not the host or a database-client tunnel.

## Screenshot recognition & records

Configure `VISION_API_KEY` in `backend\.env` with a rotated key. The default
`VISION_ENDPOINT` and `VISION_DEPLOYMENT` are listed in `backend\.env.example`;
override them if necessary. The backend uses the OpenAI-compatible chat
completions API and requires the deployment to support image inputs and JSON
responses. Never put the key in frontend code or commit it to Git.

Each upload sends the image, any additional burst frames, and the matching
approved baseline (if present) to the configured vision service. Confirm your
organization's data handling requirements before using real camera evidence.
No live API request is made during the automated tests.

After configuring SQL Server, explicitly apply the additive schema using a login
authorized to create tables and indexes:

```powershell
Set-Location backend
.\.venv\Scripts\python -m app.schema
```

This also creates `dbo.auth_users` and `dbo.auth_sessions`; the SQL statements
are in `backend/app/schema.sql` and can be run in a SQL Server client instead.
Create the first account interactively (use the application runtime login if it
has INSERT permission on `dbo.auth_users`):

```powershell
# From backend/, after applying the schema:
.\.venv\Scripts\python -m app.create_user operator
```

Enter and confirm a password of 12–256 characters at the prompts. Usernames are
case-insensitive and allow ASCII letters, numbers, `.`, `_` and `-` (1–64
characters). The command stores a salted scrypt hash, not the password. There
is no self-service registration or password reset. To disable an account, set
`is_active` to `0`; its existing sessions will immediately stop working.

Login creates an eight-hour server-side session with an HttpOnly, SameSite=Strict
browser-session cookie by default (browsers with session restore may retain it).
Selecting **Remember me for 30 days** instead creates a persistent cookie and
server-side session that expire 30 days after login, without extending on use.
Use this option only on a trusted device. Password autofill is handled by the
browser's password manager; the application never stores passwords in browser
storage. Both session types store only the token hash in `dbo.auth_sessions`,
with the choice in its `remember_me` column. Re-run `python -m app.schema` from
`backend/` before deploying this update; the additive migration defaults existing
sessions to `remember_me = 0` without changing their expiry.
The login API accepts an optional boolean `remember_me` (default `false`).
Sign-out deletes the current session, including remembered sessions.
Five failed attempts lock an account for
15 minutes. To revoke all sessions for an account, delete its rows in
`dbo.auth_sessions`. The runtime database login needs SELECT and UPDATE on
`dbo.auth_users`, and SELECT, INSERT and DELETE on `dbo.auth_sessions`.
Account creation additionally needs INSERT on `dbo.auth_users`. Keep schema
administration privileges separate from the runtime account. The application
does not provide roles, MFA, audit attribution, distributed IP rate limiting,
or account recovery. Restrict access to trusted users and networks.

This idempotent setup creates `dbo.recognition_records` (camera/shop metadata,
upload and optional capture times, overall result, review status/note/time,
analyzer version, and JPEG evidence), `dbo.recognition_checks` (one row per
signal, timestamp, and scene check), `dbo.recognition_burst_frames` (additional
burst evidence and duration), and `dbo.recognition_baselines` (baseline
approvals). It does not replace existing tables. Re-run the schema setup after
upgrading an existing deployment to add the burst and baseline tables.
Runtime operations require SELECT, INSERT and UPDATE permissions on these
tables, not schema administration permissions. Evidence and all three check
results are inserted in one transaction; a failed save never returns success.

In the application, choose **Upload screenshot**, provide shop, camera and
region, and optionally enter the actual capture time in Hong Kong time.
JPEG, PNG and single-frame WebP images are accepted up to 10 MB and 20 MP.
Images are decoded and re-encoded as JPEG without original file metadata.
Upload and capture times are stored in UTC and displayed in Asia/Hong_Kong.
Screenshots are not automatically grouped into bursts or used as baselines.

The interface offers **English**, **Traditional Chinese (繁體中文)** and
**Simplified Chinese (简体中文)** using the header language selector. The choice
is saved in browser local storage and restored on reload. Labels, accessible
names, validation/error messages, current analyzer explanations and displayed
dates change language without modifying database values. All dates remain in
Hong Kong time. Shop/camera names, filenames, operator notes and explanations
from future/unknown analyzer versions remain as originally recorded.

For manual SQL setup, open `backend/app/schema.sql` in your SQL Server client
and execute it against the configured application database. The Python setup
command reads that same file, so both installation methods use one schema.
The script creates all four recognition tables only if absent, plus their
indexes and foreign keys. `NVARCHAR` metadata
supports both Traditional and Simplified Chinese. No database changes happen
merely by opening the UI or changing its language.

The `vision-llm-v2` analyzer requests signal, timestamp reading and scene
analysis in a single model call. A matching approved baseline is supplied for
scene comparison when available. Without a baseline, a scene pass is changed
to **Not assessed**; obvious visible problems can still be flagged. The model
can make mistakes and has not been validated against the project's accuracy
KPIs. Verify results against labeled representative images before operational
use and review the live feed; a single screenshot cannot verify freeze or
flicker. An unflagged record remains **Needs verification**, not certified
healthy. Model/service errors abort the upload rather than silently saving a
success-shaped result.

### Optional manual burst checks (experimental)

Upload the first frame as the screenshot and choose 5–9 additional frames in
**Additional burst frames**. Name the additional files in capture order; the
interface sorts them by filename. Supply the actual elapsed time between the
first and last frame, from 1.5 to 2 seconds. Additional frames must each be
at most 1 MB, have the same dimensions as the first image, and use the accepted
image formats. All frames and the duration are saved with the record in one
transaction and can be reviewed in the record drawer.

All frames are included in the same model request. Burst analysis is
experimental; it may miss faults or mistake a stationary scene or lighting
changes for a fault. It cannot verify capture timing or prove a stream healthy.

### Baseline approval

In a record's review drawer, use **Load current baseline** to see the latest
approved reference for that camera. To approve the record's first image as a
new reference, choose **Approve this screenshot as baseline** and confirm.
Each approval is appended to the database; it references the original evidence
rather than replacing it. The current reference is the most recently approved
one for the exact region, shop code and camera name, including letter case.
It is not necessarily an image from yesterday.
Use consistent camera names across uploads.

Approval is not attributed to an authenticated user and the UI has no approval
history view. Access must be restricted to trusted operators. Subsequent
uploads include the approved baseline in the vision request.

### Timestamp verification

The vision model checks the current screenshot's upper-left timestamp for
presence and readability and reads its date and time; no separate OCR library
is used. The backend validates the model's normalized `YYYY-MM-DD HH:MM:SS`
reading and compares it, assumed to be Hong Kong/Macau time (UTC+8), with the
supplied actual capture time. It does not substitute upload time. The record
shows the reading, capture time, signed difference (camera minus capture time),
configured threshold and reason for the result. The default drift threshold is 180 seconds, configurable
with `VISION_DRIFT_SECONDS`. Since the form captures time to the minute, results
within 60 seconds of the threshold are **Not assessed**. Missing capture time
and unsupported or unreadable timestamps are also **Not assessed**. The
model's reading can be wrong and requires manual verification. A still image
cannot establish that the camera clock is advancing. Existing records retain
their original results and do not have the new evidence details.

The table includes evidence thumbnails, shop/region, upload time, individual
check statuses, overall result and operator review. Filters and pagination run
on the server; summary cards cover all records, not just the filtered page.
Operators can acknowledge, mark a false positive, or return a record to
unreviewed. Notes and the latest review time are retained without changing the
original recognition findings. There is no authenticated reviewer identity or
historical review audit trail yet.

API endpoints:

- `POST /api/recognitions`: multipart screenshot and metadata.
- `GET /api/recognitions`: search, outcome, region, review-status and pagination.
- `GET /api/recognitions/{id}`: saved record and individual checks.
- `GET /api/recognitions/{id}/evidence`: JPEG evidence, with no-store caching.
- `GET /api/recognitions/{id}/burst`: frame count and supplied burst duration.
- `GET /api/recognitions/{id}/burst/{index}`: additional JPEG frame, numbered
  from 1 after the primary evidence frame.
- `GET /api/recognitions/{id}/baseline`: latest baseline for the same camera;
  returns HTTP 404 when none exists.
- `POST /api/recognitions/{id}/baseline`: append an approval for that record's
  primary evidence as the camera baseline.
- `PATCH /api/recognitions/{id}/review`: review status and optional note.

Missing schema/configuration and database errors produce HTTP 503 and an
explicit UI error; there is no in-memory or mock-data fallback. Invalid uploads
return HTTP 422. Vision configuration, API and response failures return HTTP
502 and the record is not saved. Nginx allows a 20 MB multipart request; direct backend
deployments should configure an equivalent ingress request-size limit.

## Linux Docker deployment

The frontend and backend run in separate containers. The existing
`sqlserver-db` container remains independent; Compose does not create, stop,
or delete it. Restrict port 8080
to trusted internal users or a VPN and configure HTTPS at a trusted ingress.
Do not expose it directly to the internet. Also restrict the already
published SQL Server port 1433 using appropriate network controls.

Install Docker Engine, a recent Docker Compose plugin supporting `up --wait`,
Git and curl on the Linux server. The examples below assume the existing
SQL Server container is named `sqlserver-db`, SSH uses port 22, and the
deployment account can use Docker. Docker access grants host-level privileges;
use a dedicated, trusted account. Clone this repository on the server for
initial setup, and run the following from the clone's root:

```sh
sudo install -d -o "$USER" -m 700 /opt/cctv-recognition
sudo install -d -o "$USER" -m 700 /opt/cctv-recognition/releases
umask 077
if [ ! -e /opt/cctv-recognition/backend.env ]; then
  cp backend/.env.example /opt/cctv-recognition/backend.env
fi
# Edit /opt/cctv-recognition/backend.env privately.
```

Set `DB_HOST=sqlserver-db`, `DB_PORT=1433`, and the database name and dedicated
SQL login credentials in that file. Create the database and application login
in SQL Server first; the application does not create them. The file stays
outside the release directory and Git. Compose parses its env file, so quote
values as needed according to Compose env-file syntax. With certificate
validation enabled, the server certificate must be trusted by the backend
container and valid for `sqlserver-db`; alternatively use a DNS name matching
the certificate and reachable on the shared network. Set
`DB_TRUST_SERVER_CERTIFICATE=true` only when accepting the security risks of a
self-signed certificate on a trusted network.

Create a dedicated network and attach the existing SQL container once:

```sh
docker network create cctv-sql
docker network connect cctv-sql sqlserver-db
```

If the network or connection already exists, inspect it with
`docker network inspect cctv-sql` instead of creating it again. Reattach SQL
Server if its container is replaced. The backend joins this network as well
as the private application network; SQL Server's published host port is not
used by the application. Neither the database nor its volumes are managed by
this Compose project.

Build and start the application:

```sh
export BACKEND_ENV_FILE=/opt/cctv-recognition/backend.env
docker compose -p cctv-recognition -f compose.yaml -f compose.server.yaml up -d --build --wait
docker compose -p cctv-recognition -f compose.yaml -f compose.server.yaml ps
curl -fsS http://127.0.0.1:8080/api/health/database
```

The database health check verifies connectivity, not the recognition schema.
Apply `backend/app/schema.sql` to the application database using an authorized
SQL client before using uploads, and grant the runtime login SELECT, INSERT and
UPDATE on the four recognition tables and the authentication permissions
described above. Alternatively, run the schema command
in a one-off container with a separately provisioned administrator env file:

```sh
BACKEND_ENV_FILE=/secure/path/schema-admin.env docker compose -p cctv-recognition \
  -f compose.yaml -f compose.server.yaml run --rm --no-deps backend python -m app.schema
```

Keep that file restricted and remove it when no longer needed. Reapply the
additive schema when a release requires it; deployment does not run schema
changes automatically. Back up the database before upgrades.
Create an application account interactively with
`docker compose -p cctv-recognition -f compose.yaml -f compose.server.yaml run --rm --no-deps backend python -m app.create_user operator`.
Keep `AUTH_COOKIE_SECURE=true` and access the app through HTTPS.

Visit the application through the HTTPS ingress on the restricted internal network.
Nginx serves the frontend and proxies `/api/` to the backend; only the frontend
port is published by Compose. For logs or shutdown, run the same Compose command
with `logs -f` or `down` instead of `up ...`. `down` does not stop the
independently managed SQL Server container. The API liveness check is
`/api/health`; `/api/health/database` checks database connectivity.

### GitHub Actions CI/CD

The workflow runs backend tests, frontend lint/build/localization tests and
both Docker builds on pull requests and pushes to `main`. After checks pass on
`main`, it deploys that exact commit over SSH to the Linux server, rebuilds the
images there, waits for container health, and checks database connectivity.
The server must be reachable from GitHub-hosted runners on SSH port 22; the
web application can remain internal. Use a tightly restricted network path
and consider the implications of allowing GitHub-hosted runner addresses.

Before enabling automatic deployment:

1. Complete the server setup above, including the network, runtime env file,
   database schema, firewall and backup. Confirm a manual deployment works.
2. Set up a dedicated deployment SSH key and account. The account needs write
   access to `/opt/cctv-recognition/releases` and Docker access. Verify the
   server's SSH host-key fingerprint through a trusted channel. For example,
   compare `ssh-keyscan -t ed25519 SERVER_ADDRESS | ssh-keygen -lf -` with
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` run on the server.
3. In the repository's `production` environment, add these Actions secrets:
   `DEPLOY_HOST` (SSH host), `DEPLOY_USER`, `DEPLOY_SSH_PRIVATE_KEY` (the
   deploy key), and `DEPLOY_SSH_KNOWN_HOSTS` (a verified OpenSSH known_hosts
   line for that host). Never put the SQL password or SSH key in the repository.
   Configure required reviewers on the environment if deployment approval is
   desired and supported by your GitHub plan.
4. Merge the workflow into `main`. It can also be started manually from the
   Actions tab on `main`. A newer commit on `main` causes an older waiting
   deployment to be skipped.

Each deployment keeps its source under
`/opt/cctv-recognition/releases/<commit-sha>`; images are rebuilt from that
source, so they are not byte-identical to the CI build. Builds require network
access to package registries. A failed deployment or database health check
does not automatically roll back; inspect the Compose logs and redeploy a
known-good release manually if needed, by changing into its release directory
and rerunning the Compose `up -d --build --wait` command above with
`BACKEND_ENV_FILE` exported. Old release directories and Docker images are not
automatically pruned. Schema changes must remain compatible with the
application version during upgrades and any rollback.

## Checks

```powershell
# From backend/
.\.venv\Scripts\python -m unittest discover -s tests

# From frontend/
npm run lint
npm run build

# Localization tests (Node.js 22.18+ or 24+ with native TypeScript support):
node --test tests\localization.test.mjs
```

## Security

The local `Project Description/` and `Sample Camera Photos/` directories are
ignored by Git because they may contain sensitive device information or
imagery. Do not commit credentials, connection inventories or camera images.
Use a secrets manager or environment vault and read-only device accounts when
device integration is implemented.
