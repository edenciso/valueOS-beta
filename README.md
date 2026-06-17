# ValueOS Insights Beta on AWS SAM + Demo Simulation

This repo is a minimal serverless ValueOS beta that delivers first insights with:
- M2M token broker (JWT HS256 via Secrets Manager)
- M2M ingest endpoints for AI runs + business outcomes
- DynamoDB rate limiting + strict tenant_id enforcement
- Timestream metrics for near-real-time portfolio KPI queries
- Daily insights generator (Bedrock Claude) -> S3
- Evidence pack generator (HTML + PDF) -> S3
- API Gateway API Key + Usage Plan throttling at the edge
- Demo Simulation Mode: drop client sample JSON and generate a demo tenant with simulated canonical events, then on-demand insights + evidence.

## Deploy
```bash
sam build
sam deploy --guided
```

## API Key
Stack outputs include `DefaultApiKeyValue`. M2M endpoints require `x-api-key`.

## Demo simulation
Endpoint:
`POST /v1/admin/integration/wizard/simulate` (Cognito protected)

Body example:
```json
{
  "system": "hubspot",
  "demo_profile": "GTM pipeline",
  "tenant_label": "Acme Corp Demo",
  "samples": [
    { "event": "deal.updated", "dealId": "123", "stage": "proposal", "amount": 25000, "ownerEmail": "rep@acme.com" },
    { "event": "call.completed", "dealId": "123", "durationSec": 900, "notes": "pricing discussed" }
  ]
}
```

Response returns:
- `demo_tenant_id`
- one-time demo `m2m_credentials` (client_id/client_secret)
- links to:
  - `/v1/admin/portfolio?tenant_id=...`
  - `/v1/admin/insights/latest?tenant_id=...`
  - `/v1/admin/evidence/latest?tenant_id=...`

## M2M ingestion
1) Get token:
`POST /v1/m2m/token` with `x-api-key` + `client_id/client_secret`

2) Ingest:
- `POST /v1/m2m/events/ai-runs`
- `POST /v1/m2m/events/business-outcomes`

Include:
- `x-api-key: <DefaultApiKeyValue>`
- `Authorization: Bearer <token>`
