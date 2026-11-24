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
review with notes. Direct device connections, automated capture, authentication,
timestamp OCR, and baseline scene comparison are not yet implemented.
The health endpoint checks the API process, not any camera or database.

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

After configuring SQL Server, explicitly apply the additive schema using a login
authorized to create tables and indexes:

```powershell
Set-Location backend
.\.venv\Scripts\python -m app.schema
```

This idempotent setup creates `dbo.recognition_records` (camera/shop metadata,
upload and optional capture times, overall result, review status/note/time,
analyzer version, and JPEG evidence) and `dbo.recognition_checks` (one row per
signal, timestamp, and scene check). It does not replace existing tables.
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
The script creates `dbo.recognition_records` and `dbo.recognition_checks`
only if absent, plus their indexes and foreign key. `NVARCHAR` metadata
supports both Traditional and Simplified Chinese. No database changes happen
merely by opening the UI or changing its language.

The initial `screenshot-statistics-v1` analyzer screens for possible black/blank
feeds using central-image luminance and variance. This is a heuristic, not a
validated fulfillment of the PRD's accuracy KPIs. Dark scenes can be false
positives. No-signal text overlays, freeze, and flicker are not verified.
Timestamp OCR and scene comparison are explicitly **Not assessed**, with reasons
in the review drawer. A screenshot with no detected black/blank pattern is
**Needs verification**, never certified healthy. Upload time is not substituted
for the camera's capture time. OCR requires a configured engine; displacement
checks require a verified baseline and comparison engine.

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
- `PATCH /api/recognitions/{id}/review`: review status and optional note.

Missing schema/configuration and database errors produce HTTP 503 and an
explicit UI error; there is no in-memory or mock-data fallback. Invalid uploads
return HTTP 422. Nginx allows an 11 MB multipart request; direct backend
deployments should configure an equivalent ingress request-size limit.

## Linux Docker deployment

Install Docker Engine and the Docker Compose plugin on the Linux server. From
the project root, configure `backend/.env` as described above, then build and
start both services:

```sh
docker compose up --build -d
docker compose ps
```

Visit `http://SERVER_ADDRESS:8080`. The frontend container serves the built
application and proxies `/api/` to the backend container. Only the frontend port
is published; the backend is reachable on the Compose network. Check the
connection at `http://SERVER_ADDRESS:8080/api/health`. View logs with
`docker compose logs -f` and stop the services with `docker compose down`.
Check SQL connectivity at `http://SERVER_ADDRESS:8080/api/health/database`.
Compose injects the database configuration at runtime and selects Driver 18,
which the backend image installs. The database host must be reachable from the
container; use a reachable server address rather than a host-local tunnel.

The containers provide a deployment foundation, not a production security
setup. Configure HTTPS, access controls and any required firewall rules before
exposing the service. Recognition persistence is implemented, but authentication,
device integrations and automated retention are not. Restrict access to both
records and evidence; do not expose this unauthenticated internal workspace
publicly. Apply the recognition schema before using uploads.

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
