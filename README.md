# CCTV Recognition

Development starter for the retail CCTV health and visual anomaly inspection
system described in the project requirements. It contains a React, TypeScript,
Vite and Tailwind CSS frontend and a Python FastAPI backend.

## Structure

- `frontend/` - web application.
- `backend/app/` - FastAPI application.
- `backend/tests/` - backend tests.

This is a foundation only: device connections, inspection workers, database,
authentication, reporting and review workflows have not been implemented.
The health endpoint checks the API process, not any camera.

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

## Linux Docker deployment

Install Docker Engine and the Docker Compose plugin on the Linux server. From
the project root, build and start both services:

```sh
docker compose up --build -d
docker compose ps
```

Visit `http://SERVER_ADDRESS:8080`. The frontend container serves the built
application and proxies `/api/` to the backend container. Only the frontend port
is published; the backend is reachable on the Compose network. Check the
connection at `http://SERVER_ADDRESS:8080/api/health`. View logs with
`docker compose logs -f` and stop the services with `docker compose down`.

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
