# CCTV Recognition

Development starter for the retail CCTV health and visual anomaly inspection
system described in the project requirements. It contains a React, TypeScript,
Vite and Tailwind CSS frontend and a Python FastAPI backend.

## Structure

- `frontend/` - web application.
- `backend/app/` - FastAPI application.
- `backend/tests/` - backend tests.

This is a foundation only: device connections, inspection workers,
authentication, reporting and review workflows have not been implemented.
SQL Server connectivity is available; no application tables or migrations are
created. The health endpoint checks the API process, not any camera or database.

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
exposing the service; authentication, persistence and device integrations are
not yet implemented.

## Checks

```powershell
# From backend/
.\.venv\Scripts\python -m unittest discover -s tests

# From frontend/
npm run lint
npm run build
```

## Security

The local `Project Description/` and `Sample Camera Photos/` directories are
ignored by Git because they may contain sensitive device information or
imagery. Do not commit credentials, connection inventories or camera images.
Use a secrets manager or environment vault and read-only device accounts when
device integration is implemented.
