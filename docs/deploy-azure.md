# Deploy on Azure App Service

Deploy the app as one Docker image on Azure App Service (Web App for Containers) with
HTTPS on the default `*.azurewebsites.net` address. The image contains the built UI and the
Python server, runs as non-root and holds no secrets; configuration comes from app
settings. State lives in one process and one SQLite file, so run exactly one instance and
never scale out.

The guide assumes the image is built from this repository and pushed to GitHub Container
Registry (GHCR) under your own account; any other registry works the same way. For other
hosts, see [Any container host](../README.md#any-container-host) in the README.

## 1. First deployment

1. Build and push the image from the repository root.

   ```bash
   gh auth refresh -h github.com -s write:packages
   gh auth token | docker login ghcr.io -u <github-user> --password-stdin
   docker build --platform linux/amd64 -t ghcr.io/<github-user>/ai-lions-den:v1 .
   docker push ghcr.io/<github-user>/ai-lions-den:v1
   ```

   `--platform linux/amd64` is required on Apple silicon; an arm64 image fails with an exec
   format error.

2. If your image is private (the GHCR default), create a classic GitHub token with only
   `read:packages` (Settings > Developer settings > Personal access tokens (classic)); add
   `repo` if a private repository reports `unauthorized`. Azure uses it to pull the image.
   For a public image, skip this step and choose **Public** access below.

3. In the portal choose **Create a resource > Web App**:

   | Tab | Field | Value |
   |---|---|---|
   | Basics | Resource group | `<resource-group>`, an existing or a new one |
   | Basics | Publish / OS | Container / Linux |
   | Basics | Name | `<app-name>`, globally unique |
   | Basics | Region | A region close to your audience, for example West Europe |
   | Basics | Plan | New Linux plan, **Basic B1** (not the preselected Premium V3 P0v3, about 4x the price) |
   | Basics | Zone redundancy | Off |
   | Database | Create a Database | Off |
   | Container | Sidecar support | Off |
   | Container | Image source / Access | Other container registries / Private (or Public) |
   | Container | Registry server URL | `https://ghcr.io` |
   | Container | Username / Password | `<github-user>` / the token (private image only) |
   | Container | Image and tag | `<github-user>/ai-lions-den:v1` |
   | Container | Startup command | Empty |
   | Networking | Public access / VNet | On / Off |
   | Monitor + secure | Application Insights | No |

   The default hostname `<app-name>-<random>.<region>-01.azurewebsites.net` cannot be
   changed later.

4. Under **Settings > Environment variables > App settings**, add the settings from
   below (start with `DEMO_MODE=true`). Edit the existing
   `WEBSITES_ENABLE_APP_SERVICE_STORAGE` to `true` instead of adding a duplicate and leave
   `DOCKER_REGISTRY_SERVER_*` alone. Click **Apply**, then **Confirm**; otherwise nothing
   is saved.

   | Setting | Value |
   |---|---|
   | `WEBSITES_ENABLE_APP_SERVICE_STORAGE` | `true` (edit the existing entry) |
   | `WEBSITES_PORT` | `3000` |
   | `SESSION_DB` | `/home/data/session.db` |
   | `DEMO_MODE` | `true` for the first check, `false` for the real model |
   | `PUBLIC_BASE_URL` | `https://<default-domain>`, no trailing slash |
   | `MODERATOR_PASSWORD` | Long random secret (`openssl rand -base64 18`) |
   | `JOIN_SHORT_URL` | Optional short link (redirecting to `<PUBLIC_BASE_URL>/team`) shown on the projector; the QR code still points at the app |

5. Under **Settings > Configuration > General settings** set Always On **On**, HTTPS Only
   **On**, Minimum inbound TLS **1.2**, FTP state **Disabled**, and save. Then enable
   **Monitoring > Health check** with path `/api/health`.

6. Check `https://<default-domain>/api/health`. It should return
   `{"ok":true,"phase":"lobby","teams":0,"demo":true,...}`. The first start takes a few
   minutes (**Monitoring > Log stream** shows progress). Open `/screen`, confirm the QR code
   leads to the same address, register a test team, and log in at `/moderator`.

7. For the real model set the three `OPENAI_*` settings and `DEMO_MODE=false`, then
   **Apply** and **Confirm**. Verify `/api/health` reports `"demo":false` and a test run
   returns real jury output.

   | Setting | Value |
   |---|---|
   | `OPENAI_API_KEY` | The API key. A secret. |
   | `OPENAI_BASE_URL` | Empty for the OpenAI API, or `https://<resource>.openai.azure.com/openai/v1/` for Azure OpenAI |
   | `OPENAI_MODEL` | The model name, or the Azure OpenAI deployment name |

## 2. Update to a new version

Settings and the `/home` session survive; do not update mid-session.

1. Find the current tag in the web app's **Deployment Center** or in your registry's
   package list. Use the next number below.

2. Build and push.

   ```bash
   docker build --platform linux/amd64 -t ghcr.io/<github-user>/ai-lions-den:v<N> .
   docker push ghcr.io/<github-user>/ai-lions-den:v<N>
   ```

3. In **Deployment Center** change the tag to `v<N>` and **Save**. App Service pulls the new image and restarts the app automatically (about 1-2 minutes of downtime, which is why you do not update mid-session). Rollback is the same step with the old tag.
4. Check `/api/health`. If it does not come back or the old version is still served, use **Overview > Restart**.
5. Hard reload (Cmd+Shift+R) every open page, especially `/moderator` and `/screen`;
   pages loaded before the update keep the old UI.

## 3. Pause and resume

Azure bills the App Service plan, not the web app: stopping the app alone saves nothing.
To pause cheaply, move the plan to the free tier. Settings and the session survive.

| Plan | Cost | Use |
|---|---|---|
| Basic B1 | about 13 USD per month (about 0.02 USD per hour) | Testing and events |
| Free F1 | free | Paused only (no Always On, too slow for a live session) |

Prices are list-price estimates and change over time.

**Pause** (after a test or an event):

1. **Configuration > General settings**: Always On **Off**, save (F1 does not allow it).
2. **Overview > Stop**.
3. App Service plan > **Scale up**: **Free F1**.

**Resume** (about 30 minutes before use):

1. App Service plan > **Scale up**: **Basic B1**.
2. **Configuration > General settings**: Always On **On**, save.
3. **Overview > Start**, then check `/api/health`.

With a custom domain or managed certificate Azure may refuse the switch to F1; then stay
on B1.

**Delete permanently:** if you created the resource group only for this app, delete the
group. Otherwise delete only this web app and its App Service plan.

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| Exec format error | Image built without `--platform linux/amd64` |
| `unauthorized` pulling the image | Token expired or missing `read:packages` (and `repo` for a private repository); create a new one and update the password in **Deployment Center** or `DOCKER_REGISTRY_SERVER_PASSWORD` |
| Settings seem ignored | **Apply** and **Confirm** were not both clicked |
| Container never becomes healthy | `WEBSITES_PORT=3000`; check **Log stream** |
| Moderator page shows the old UI after an update | Hard reload |
| Resource group or plan creation refused | Your account needs the Contributor role (Reader is not enough) |
| Container Registry creation fails | Subscription needs the `Microsoft.ContainerRegistry` provider registered (subscription-level rights) |

Alternative: Azure Container Registry (Basic, admin user enabled) instead of GHCR avoids
the token but costs a few USD per month; push to `<registry>.azurecr.io/ai-lions-den:v<N>`
and choose **Azure Container Registry** as the image source.
