# Local / runtime data

| File | Purpose |
|------|---------|
| `submissions.example.json` | Committed demo seed for local UI |
| `submissions.json` | Local runtime store (**gitignored**) |

```powershell
copy data\submissions.example.json data\submissions.json
```

Production uses Google Sheets (`SOLUTIONS_HUB_SHEET_ID`), not these files.
