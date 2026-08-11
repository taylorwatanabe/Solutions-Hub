# ECS Express Mode setup (Solution Hub)

This app does **not** provision AWS resources. Create them once (console or IaC), then use `deploy-ecs.ps1`.

## 1. ECR

Create repository **`solution-hub`** in `us-east-2`.

Image URI after push:

```text
422799216424.dkr.ecr.us-east-2.amazonaws.com/solution-hub:latest
```

## 2. ECS Express Mode

| Setting | Value |
|---------|--------|
| Cluster | `Solution-Hub` (**new** Express Mode cluster; ECS names cannot contain spaces) |
| Service name | `Solution-Hub` |
| Container port | `8080` |
| Image | ECR URI above |
| Health check | `GET /healthz` (also `/health`, `/ping`, `/api/health`) |
| Public URL | `https://so-f58011ac242d43a99fb53eb9959c04df.ecs.us-east-2.on.aws` |

One-shot create (already applied once):

```powershell
aws ecs create-express-gateway-service --region us-east-2 --cli-input-json file://scripts/express-create-input.json --monitor-mode TEXT-ONLY
```

CPU/memory use Fargate units (`1024` / `2048`), not `1` / `2`.

### Task environment (plain)

| Name | Notes |
|------|--------|
| `AWS_REGION` | `us-east-2` |
| `SOLUTIONS_HUB_SHEET_ID` | Google Sheet ID for the Kanban dataset |
| `SOLUTIONS_HUB_SHEET_TAB` | Tab name (default `Submissions`) |
| `TRUST_PROXY_HEADERS` | `1` (Dockerfile also defaults this) |

### Task environment (Secrets Manager → type **Secret**)

Reuse the **same GCP service account** already used by QC Dashboard / the CAT-2 AWS environment:

| Name | Notes |
|------|--------|
| `AWS_GCP_SERVICE_ACCOUNT_SECRET_ID` | Map to the existing Secrets Manager secret that holds the GCP SA JSON. At runtime ECS injects the **plaintext JSON** into this env var; the app copies it to `GCP_SERVICE_ACCOUNT_JSON`. |

Do **not** create a separate Google service account for Solutions Hub unless required. Share the Solutions Hub sheet with the existing SA `client_email` (Editor).

If you instead pass a secret **name/ARN** (not inlined JSON), grant the task role `secretsmanager:GetSecretValue` on that secret.

### Optional local override (not for ECS)

- `GCP_SERVICE_ACCOUNT_JSON` — full SA JSON string
- `GOOGLE_APPLICATION_CREDENTIALS` — path to SA key file
- `ALLOW_LOCAL_DATA_STORAGE=1` without `SOLUTIONS_HUB_SHEET_ID` — JSON file under `data/` for UI demos

## 3. Google Sheet schema

### Compact mode (Friction-to-Solutions replies) — current default

| Setting | Value |
|---------|--------|
| Sheet | `1sR1CTeznF4-8Az3WhGwgaAI4eMD6JEfAUhzgVpEvRSs` |
| Tab | gid `439459761` |
| Range | `AI2:AK` (row 2 downward) |
| Field map | `SOLUTIONS_HUB_COLUMN_FIELDS=status,department,problem` → AI, AJ, AK |

Share the sheet with the GCP SA `client_email` (Editor). Then set ECS env vars (see `scripts/express-update-sheet-input.json`) and inject `AWS_GCP_SERVICE_ACCOUNT_SECRET_ID` as a **Secret**.

If AI/AJ/AK are not status/department/problem, change `SOLUTIONS_HUB_COLUMN_FIELDS` to the correct order (comma-separated field names).

### Full-sheet mode (optional)

Clear `SOLUTIONS_HUB_DATA_RANGE` and use a dedicated tab with header row:

`id`, `submitted_at`, `submitter_name`, `submitter_email`, `department`, `problem`, `option_a`, `option_b`, `option_c`, `recommendation`, `resources`, `estimated_value`, `status`, `upvotes`, `score_total`, `sprint_lead`, `coaching_notes`, `roi_tier`

Status values must be exactly (aliases like `Pilot` / `Archived` are normalized):

- Received
- In Review
- In Progress / Pilots
- Implemented / Wins
- NA / Archived

Seed from CSV (full-sheet mode only):

```powershell
python scripts/seed_from_csv.py path\to\verified.csv
```

## 4. Deploy

```powershell
.\deploy-ecs.cmd
```

Or CodeBuild with `buildspec.yml` (privileged mode on).

After deploy, verify:

```text
GET https://<public-host>/api/build-info
GET https://<public-host>/healthz
```

## 5. Auth / email (deferred)

MVP has **no** Google OAuth / allowlist (unlike CAT-2). Status and department edits are open on the intranet UI.

SLA confirmation email is a **stub** (`integrations/notifications.py`); production receipts/alerts are expected via Sunrun Current later.
