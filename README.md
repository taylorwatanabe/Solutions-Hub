# Solutions Hub

Friction-to-Solutions portal: Kanban pipeline by status + 1-3-1 intake.

**Repo:** [taylorwatanabe/Solutions-Hub](https://github.com/taylorwatanabe/Solutions-Hub)

## Features (MVP)

- Status Kanban: Received → In Review → In Progress / Pilots → Implemented / Wins → NA / Archived
- Department filters and quantity chips
- 1-3-1 intake form (problem / three options / recommendation)
- Card upvote + status/department/notes edits (no auth in MVP)
- Google Sheets sync (or local JSON for development)
- ECS Express Mode deploy scripts (`Solution Hub` cluster/service)

## Local run

```powershell
copy .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy data\submissions.example.json data\submissions.json
python app.py
```

Open http://127.0.0.1:8080

Smoke check:

```powershell
$env:ALLOW_LOCAL_DATA_STORAGE="1"
python scripts/smoke_test.py
```

## Production data

Google Sheets is the source of truth. Reuse the **same GCP service account** as QC Dashboard / CAT-2 (via `AWS_GCP_SERVICE_ACCOUNT_SECRET_ID`). See [docs/ecs-express-setup.md](docs/ecs-express-setup.md).

Seed a verified CSV:

```powershell
python scripts/seed_from_csv.py path\to\dataset.csv
```

## Deploy (ECS Express Mode)

| Resource | Value |
|----------|--------|
| Cluster | `Solution-Hub` |
| Service | `Solution-Hub` |
| ECR | `solution-hub` |
| Region | `us-east-2` |
| Port / health | `8080` / `/healthz` |
| Public URL | https://so-f58011ac242d43a99fb53eb9959c04df.ecs.us-east-2.on.aws |

```powershell
.\deploy-ecs.cmd
```

CodeBuild: [buildspec.yml](buildspec.yml)

Recreate Express Mode (if needed): `scripts/express-create-input.json`

## API

| Route | Description |
|-------|-------------|
| `GET /api/board?department=` | Kanban columns + counts |
| `POST /api/submissions` | Create 1-3-1 pitch (`Received`) |
| `PATCH /api/submissions/<id>` | Update status / department / notes |
| `POST /api/submissions/<id>/upvote` | Increment upvotes |
| `GET /api/build-info` | Service + build id |
| `GET /healthz` | Load balancer health |

## GitHub

- CI: [.github/workflows/ci.yml](.github/workflows/ci.yml) runs `scripts/smoke_test.py` on push/PR to `main`
- Dependabot: weekly pip + monthly Actions updates
- Do **not** commit `.env`, service-account JSON, or `data/submissions.json`

## MVP notes

- No Google OAuth (intranet-trust edits for status/department).
- SLA email is a stub pending Current integration.
