# Excel → CSV Foundry Agent

A Python **AI agent hosted on Microsoft Azure AI Foundry** (Foundry **Hosted
Agents**) that takes an Excel filename, fetches that file from **Azure Blob
Storage**, and converts **every sheet into its own CSV file**. A workbook with 3
sheets produces 3 CSV files. It is called from **Azure Logic Apps** with a single
HTTP action.

> **Hosting:** this runs as a **Foundry Hosted Agent** — your container image runs
> on Microsoft-managed, per-session-isolated infrastructure that Foundry
> provisions and scales for you. **No Azure Container App is created or used.**

```
┌──────────────┐  HTTP POST /invocations   ┌──────────────────────────────┐
│  Logic App   │ ──────{ "filename" }─────▶ │  Foundry Hosted Agent        │
│ (HTTP action │ ◀─────{ files:[...] }───── │  (your container, run BY     │
│  + Mgd Id.)  │                            │   Foundry Agent Service)     │
└──────────────┘                            └───────────────┬──────────────┘
                                                            │ convert (deterministic)
                          ┌──────────────┐  download        ▼
                          │ Blob Storage │ ◀────────  ┌──────────────┐
                          │  incoming/   │            │  converter    │
                          │  converted/  │ ─────────▶ │ (pandas)      │
                          └──────────────┘   upload   └──────────────┘
                                        3 sheets → 3 CSVs
```

---

## Contents

1. [How it works](#how-it-works)
2. [Why the Invocations protocol](#why-invocations)
3. [Repository layout](#layout)
4. [Prerequisites](#prereqs)
5. [Azure resources](#resources)
6. [Configuration](#config)
7. [Run & test locally](#local)
8. [Deploy to Foundry (Hosted Agent)](#deploy)
9. [Grant access (RBAC)](#rbac)
10. [Call it from Logic Apps](#logic-apps)
11. [Invocation contract](#contract)
12. [Optional: put a model in the loop](#model)
13. [Troubleshooting](#troubleshooting)
14. [Versions & preview notes](#versions)
15. [Security & cost](#security)

---

<a name="how-it-works"></a>
## 1. How it works

* The agent is packaged as a container that implements a **Foundry Hosted Agent
  protocol** and is deployed into your Foundry project. Foundry runs and scales
  it (scale-to-zero when idle, cold-resume on the next call).
* On each call it receives `{ "filename": "..." }`, downloads that blob from the
  **input** container, converts **one sheet → one CSV** (headers/columns
  preserved), uploads each CSV to the **output** container, and returns the exact
  list of files it wrote.
* Your **Logic App** invokes it with a single HTTP action, authenticated with the
  Logic App's **managed identity**.

The conversion itself is plain, unit-tested Python (`pandas` + `openpyxl`). It is
deterministic — no model guesses at your data.

<a name="why-invocations"></a>
## 2. Why the Invocations protocol (not a chat model)

Foundry Hosted Agents can speak two protocols:

* **Responses** — OpenAI-compatible, conversational (`/responses`). Best when a
  model reasons over the request.
* **Invocations** — full control over a custom request/response body
  (`/invocations`). Best for **non-conversational** processing.

Converting Excel → CSV is a deterministic transform with a fixed input/output
shape, so this project uses **Invocations**. You still get a genuine Foundry
Hosted Agent; you just don't pay for or wait on an LLM that has nothing to decide.
If you *do* want a model in the loop (e.g. "only convert sheets matching X"), see
[section 12](#model).

<a name="layout"></a>
## 3. Repository layout

```
excel-to-csv-foundry-agent/
├── app/
│   ├── host.py          # Hosted-agent entrypoint: POST /invocations handler
│   ├── converter.py     # Deterministic Excel → CSV (the actual work)
│   ├── blob_storage.py  # Download Excel / upload CSVs (Managed Identity)
│   └── config.py        # Settings from environment variables
├── scripts/
│   └── invoke_local.py  # Send a test invocation to a locally-running host
├── tests/
│   └── test_converter.py
├── logicapp/
│   └── workflow.sample.json   # Sample Logic App that calls /invocations
├── azure.yaml           # REFERENCE azd config for the hosted-agent deploy
├── Dockerfile           # linux/amd64 container running app.host
├── requirements.txt
└── .env.example
```

<a name="prereqs"></a>
## 4. Prerequisites

* Azure subscription with access to **Microsoft Foundry** (Hosted Agents are in
  preview).
* **Azure CLI** 2.80+ and the **Azure Developer CLI (`azd`)** with the agent
  extension: `azd ext install azure.ai.agents`.
* **Python 3.10+** for local development.
* Role **Foundry Project Manager** (formerly *Azure AI Project Manager*) at
  project scope, to deploy a hosted agent.
* Docker is **optional** — `azd` builds the image remotely with ACR Tasks. If you
  build locally on Apple Silicon, target amd64: `docker build --platform linux/amd64 .`.

<a name="resources"></a>
## 5. Azure resources

**Foundry project + model:** create (or reuse) a Foundry project. A chat model
deployment is only needed for the optional model path in section 12; the default
Invocations agent doesn't require one. `azd ai agent init` can create the project
and model for you.

**Storage account + containers:**
```bash
az storage account create -n <youraccount> -g <rg> -l <region> --sku Standard_LRS
az storage container create --account-name <youraccount> --name incoming  --auth-mode login
az storage container create --account-name <youraccount> --name converted --auth-mode login
```
`incoming` holds the `.xlsx` files; `converted` receives the `.csv` files (they
can be the same container — outputs can take a prefix).

<a name="config"></a>
## 6. Configuration

Environment variables (see `.env.example`). On Foundry these are declared in
`azure.yaml` and injected into the container; locally they come from `.env`.

| Variable | Required | Description |
|---|---|---|
| `AZURE_STORAGE_ACCOUNT_URL` | yes* | `https://<acct>.blob.core.windows.net` |
| `AZURE_STORAGE_CONNECTION_STRING` | no | Local-dev fallback only; overrides Managed Identity |
| `INPUT_CONTAINER` | yes | Incoming Excel container (default `incoming`) |
| `OUTPUT_CONTAINER` | yes | Output CSV container (default `converted`) |
| `OUTPUT_PREFIX` | no | Virtual folder prefix for outputs, e.g. `csv/` |
| `CSV_NAME_TEMPLATE` | no | Output naming. Default `{stem}__{sheet}.csv` |
| `LOG_LEVEL` | no | `INFO` by default |
| `FOUNDRY_PROJECT_ENDPOINT` | auto | Injected by Foundry at runtime; don't set manually |

\* Required unless you use a connection string locally.

<a name="local"></a>
## 7. Run & test locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Converter unit tests (no Azure needed):
pytest -q

# Run the hosted-agent server locally (serves on :8088):
cp .env.example .env      # fill in values
az login                  # DefaultAzureCredential uses this for blob access
python -m app.host
```
In another terminal:
```bash
python -m scripts.invoke_local data.xlsx
# POSTs {"filename":"data.xlsx"} to http://localhost:8088/invocations
```
The framework also exposes `GET /readiness` for health checks.

<a name="deploy"></a>
## 8. Deploy to Foundry (Hosted Agent)

The recommended, current path is the `azd` agent extension. Scaffold once to get
a correct, up-to-date `azure.yaml`/Dockerfile for the preview, then drop this
repo's code in:

```bash
azd ext install azure.ai.agents        # install the agent extension
azd ai agent init                      # choose: Python, custom / bring-your-own
#   -> replace the generated sample app/ + requirements.txt with THIS repo's,
#      set protocol = invocations, and add the env vars from azure.yaml here
azd ai agent up                        # builds image (ACR Tasks) + deploys to Foundry
```

`azd` builds the image, pushes it to ACR, creates the hosted-agent version on
Foundry-managed infrastructure, and wires up most RBAC. When it finishes it
prints the agent's **invoke endpoint** — copy that; the Logic App needs it.

The included [`azure.yaml`](azure.yaml) is a **reference**: treat the file that
`azd ai agent init` generates as the source of truth and merge the `env:` block
into it.

> Prefer the Python SDK or REST to deploy instead of `azd`? Both are supported —
> see Microsoft's "Deploy a hosted agent" doc. The container in this repo is a
> standard Invocations image, so any of the documented deploy paths work.

<a name="rbac"></a>
## 9. Grant access (RBAC)

**a) Deploying** requires **Foundry Project Manager** at project scope (you).

**b) The agent's runtime identity** needs:
* **Foundry User** (formerly *Azure AI User*) on the project — for model/artifact
  access. `azd`/VS Code usually assign this automatically.
* **Storage Blob Data Contributor** on your storage account — so it can read the
  Excel and write the CSVs:
  ```bash
  # <AGENT_PRINCIPAL_ID> = the hosted agent's managed identity object id
  STG_ID=$(az storage account show -n <acct> -g <rg> --query id -o tsv)
  az role assignment create --assignee <AGENT_PRINCIPAL_ID> \
    --role "Storage Blob Data Contributor" --scope $STG_ID
  ```

**c) The Logic App's identity** needs **Foundry User** on the project so it's
allowed to invoke the agent. Enable a system-assigned identity on the Logic App,
then assign that role at the project scope.

> Foundry RBAC roles were recently renamed (Foundry User / Project Manager were
> previously Azure AI User / Project Manager). If a role name isn't found, use the
> other name. Blob role assignments take a minute or two to propagate.

<a name="logic-apps"></a>
## 10. Call it from Logic Apps

Your Logic App calls the hosted agent's invoke endpoint with an **HTTP** action,
authenticated by **managed identity**.

1. **Trigger** — *When a HTTP request is received*, schema `{ "filename": "string" }`
   (or a Blob trigger if you want it fully automated).
2. **HTTP** action:
   * Method: `POST`
   * URI: the **invoke endpoint** from the `azd` deploy output (the Foundry
     project endpoint + the agent's `/invocations` route).
   * Body: `{ "filename": "@{triggerBody()?['filename']}" }`
   * Authentication: **Managed identity**, audience `https://ai.azure.com`
     (if you get 401, try `https://cognitiveservices.azure.com`).
3. Optionally **Parse JSON** the response and **Respond** to the caller.

A ready-to-paste definition is in
[`logicapp/workflow.sample.json`](logicapp/workflow.sample.json) — replace
`<AGENT_INVOKE_URL>`.

<a name="contract"></a>
## 11. Invocation contract

Request (`POST /invocations`):
```json
{ "filename": "data.xlsx" }
{ "filename": "CC MM LTP AUG.xlsx" }
```
`filename` is the blob name in `INPUT_CONTAINER`; it may include a path, e.g.
`"reports/2026/data.xlsx"`. (The handler also accepts `blob_name`, `file`, or a
nested `input` for flexibility.)

Success `200`:
```json
{
  "status": "succeeded",
  "source_blob": "data.xlsx",
  "output_container": "converted",
  "sheet_count": 3,
  "files": [
    { "sheet_name": "Customers",  "csv_blob_name": "data__Customers.csv",  "csv_url": "https://.../converted/data__Customers.csv",  "rows": 2, "columns": 2 },
    { "sheet_name": "Inventory",  "csv_blob_name": "data__Inventory.csv",  "csv_url": "https://.../converted/data__Inventory.csv",  "rows": 3, "columns": 2 },
    { "sheet_name": "Sales 2026", "csv_blob_name": "data__Sales_2026.csv", "csv_url": "https://.../converted/data__Sales_2026.csv", "rows": 2, "columns": 2 }
  ]
}
```
Errors: `400` (missing filename / bad JSON), `404` (blob not found), `500`
(conversion error) — each as `{ "status": "failed", "error": "..." }`.

<a name="model"></a>
## 12. Optional: put a model in the loop

If you want an LLM to reason over the request (route, filter sheets, validate),
switch to the **Responses** protocol and expose `convert_excel_to_csv` as a
**tool** the model calls. Sketch:

```python
# pip install azure-ai-agentserver-responses agent-framework  (preview)
from azure.ai.agentserver.responses import (
    CreateResponse, ResponseContext, ResponsesAgentServerHost, TextResponse,
)
from app.converter import convert_excel_to_csv   # the same deterministic tool

app = ResponsesAgentServerHost()

@app.response_handler
async def handler(request: CreateResponse, context: ResponseContext, cancel):
    # Wire convert_excel_to_csv as a tool for a Foundry model (via the Agent
    # Framework), let the model call it, then return the tool's result.
    ...

app.run()
```
Keep returning the **tool's** structured output (not the model's paraphrase) so
callers always get the real file list. The Agent Framework Foundry-hosting
integration is prerelease — scaffold the current version with `azd ai agent init`
and choose a Responses/Agent-Framework template.

<a name="troubleshooting"></a>
## 13. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `404 Blob '...' not found` | Wrong `INPUT_CONTAINER` or filename (case-sensitive, include any path). |
| `403` on blob ops | Agent identity missing **Storage Blob Data Contributor** (or not propagated yet). |
| `401` invoking from Logic App | Logic App identity missing **Foundry User** on the project, or wrong `audience` — try the alternate value. |
| Deploy rejected — ARM image | Hosted agents require **linux/amd64**. Build with `--platform linux/amd64` (or let ACR Tasks build). |
| Can't deploy — permissions | You need **Foundry Project Manager** at project scope. |
| Broken accents in CSV | CSVs are UTF-8 **with BOM** (`utf-8-sig`) for Excel; read them as UTF-8 downstream. |
| Empty sheet → header-only CSV | Expected (0 data rows). |

Local run prints OpenTelemetry export noise on exit (it tries to reach Azure's
metadata endpoint). That's harmless outside Azure.

<a name="versions"></a>
## 14. Versions & preview notes

* **Foundry Hosted Agents are in preview**; the managed hosting service is GA, but
  tooling (`azd ai agent`, the `agent-framework` Foundry-hosting integration) and
  the exact `azure.yaml` schema evolve. Scaffold with `azd ai agent init` to get
  current shapes, and confirm the invoke URL + managed-identity audience from your
  own deploy output.
* Runtime libraries used here: `azure-ai-agentserver-invocations` (with
  `-core`), `pandas`, `openpyxl`, `azure-storage-blob`, `azure-identity`. Pins are
  in `requirements.txt`.
* Nothing in `app/converter.py` or `app/blob_storage.py` is protocol-specific, so
  if the hosting API shifts, only `app/host.py` (and `azure.yaml`) need attention.

<a name="security"></a>
## 15. Security & cost

* **No secrets in code.** Auth uses `DefaultAzureCredential` → the hosted agent's
  managed identity in Foundry; the connection-string path is a local-dev fallback.
* **Least privilege.** The agent identity only needs Blob data access on one
  storage account and Foundry User on one project. The Logic App identity only
  needs Foundry User to invoke.
* **Cost.** Hosted-agent billing is consumption of CPU/memory during active
  sessions, and it scales to zero when idle. The default Invocations path uses **no
  model tokens**; only the optional model path (section 12) does.
