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
| Cluster | `Solution Hub` (**new** Express Mode cluster) |
| Service name | `Solution Hub` |
| Container port | `8080` |
| Image | ECR URI above |
| Health check | `GET /healthz` (also `/health`, `/ping`, `/api/health`) |

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

Header row (tab `Submissions`):

`id`, `submitted_at`, `submitter_name`, `submitter_email`, `department`, `problem`, `option_a`, `option_b`, `option_c`, `recommendation`, `resources`, `estimated_value`, `status`, `upvotes`, `score_total`, `sprint_lead`, `coaching_notes`, `roi_tier`

Status values must be exactly:

- Received
- In Review
- In Progress / Pilots
- Implemented / Wins
- NA / Archived

Seed from CSV:

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
