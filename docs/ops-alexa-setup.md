# Jarvis live setup (Alexa + Google) — detailed checklist

Goal: say something to an Echo/Show → Alexa skill → Jarvis → spoken reply (and calendar/task side effects when approved).

Do these sections **in order**. Check each box as you go.

Current machine status (as of last local setup):
- Docker stack was started successfully
- Dashboard: http://localhost:8006/
- Alexa ingress (local only): http://localhost:8002/health
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` are still empty in `.env`
- Google OAuth status was `connected: false`

---

## Part A — Confirm Jarvis is running locally

### A1. Open Docker Desktop
1. Open **Docker Desktop** on the Mac Mini.
2. Wait until it says **Engine running**.

### A2. Start (or restart) the stack
In Terminal:

```bash
cd ~/Projects/jarvis
docker compose up -d --build
docker compose ps
```

You want every service `Up` (or `running`):
- `alexa-ingress`
- `web`
- `core`
- `connectors-google`
- `scheduler`
- `policy`
- `postgres`
- `redis`

If `core`, `connectors-google`, or `scheduler` show `Exit`, run:

```bash
docker compose logs --tail=80 core connectors-google scheduler
```

### A3. Health checks

```bash
curl -s http://localhost:8002/health
curl -s http://localhost:8006/health
```

Both should return something like `{"status":"ok"}`.

Open the dashboard in Chrome: http://localhost:8006/

### A4. Security checklist (optional but useful)

```bash
cd ~/Projects/jarvis
.venv/bin/python scripts/jarvis_security_check.py
```

Expect mostly `[PASS]`. Do not continue if `alexa-ingress` or `web` failed to start.

---

## Part B — Google Cloud OAuth (calendar / tasks / gmail)

Jarvis needs a Google **OAuth Web client**. Without this, voice calendar reads still “work” but return stub data.

### B1. Open Google Cloud Console
1. Go to https://console.cloud.google.com/
2. Sign in with the household Google account you want Jarvis to use.

### B2. Create or select a project
1. Top bar → project picker → **New Project**
2. Project name: `jarvis-household` (any name is fine)
3. Click **Create**
4. Make sure that project is selected in the top bar

### B3. Enable APIs
1. Left menu → **APIs & Services** → **Library**
2. Search and enable each of these (click → **Enable**):
   - **Google Calendar API**
   - **Google Tasks API**
   - **Gmail API**

### B4. Configure the OAuth consent screen
1. Left menu → **APIs & Services** → **OAuth consent screen**
2. If asked for User Type:
   - Choose **External** (typical for personal Gmail)
   - Or **Internal** only if this is a Google Workspace org you admin
3. Click **Create**
4. App information:
   - App name: `Jarvis`
   - User support email: your email
   - Developer contact email: your email
5. Click **Save and Continue**
6. **Scopes** → **Add or Remove Scopes** → add:
   - `.../auth/calendar`
   - `.../auth/tasks`
   - `.../auth/gmail.readonly`  
   (You can also skip picking scopes here; Jarvis requests them at connect time. Adding them makes the consent screen clearer.)
7. **Save and Continue**
8. **Test users** (required while app is in Testing):
   - **Add Users**
   - Add every Google account that will connect (at least the household account)
9. **Save and Continue** → back to dashboard

Leave publishing status as **Testing** for now. That is fine for household use.

### B5. Create OAuth client credentials
1. Left menu → **APIs & Services** → **Credentials**
2. **+ Create Credentials** → **OAuth client ID**
3. Application type: **Web application**
4. Name: `Jarvis local web`
5. Under **Authorized redirect URIs** → **Add URI**:

```text
http://localhost:8006/oauth/google/callback
```

6. Click **Create**
7. Copy:
   - **Client ID**
   - **Client secret**  
   (You can reopen them later from the Credentials list.)

### B6. Put credentials into Jarvis `.env`
1. Open `~/Projects/jarvis/.env` in Cursor / VS Code / TextEdit
2. Set these lines (keep the redirect URI exact):

```bash
GOOGLE_CLIENT_ID=paste-client-id-here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=paste-client-secret-here
GOOGLE_REDIRECT_URI=http://localhost:8006/oauth/google/callback
GOOGLE_ACCOUNT_KEY=household
```

3. Save the file
4. Restart the Google connector + web:

```bash
cd ~/Projects/jarvis
docker compose up -d connectors-google web
```

### B7. Connect the household Google account
1. In Chrome (on the Mac Mini), open:

```text
http://localhost:8006/oauth/google/start
```

2. You should be redirected to Google sign-in / consent
3. Choose the household Google account (must be a Test user from B4)
4. Review requested access (Calendar, Tasks, Gmail readonly) → **Allow** / **Continue**
5. Browser should land back on Jarvis and show JSON like:

```json
{"status":"connected","account_key":"household"}
```

6. Verify:

```bash
curl -s "http://localhost:8006/oauth/google/status"
```

Expect `"connected": true`.

### B8. Optional: pin specific calendars / task lists
If you leave these blank, Jarvis can still run, but calendar reads/writes are more reliable once IDs are set.

#### Find a Google Calendar ID (web)
1. Open https://calendar.google.com in Chrome (signed into the household Google account you connected in B7)
2. On the left, under **My calendars** or **Other calendars**, hover the calendar you want → click the three-dot menu → **Settings and sharing**
   - Or: gear icon (top right) → **Settings** → select the calendar in the left sidebar
3. Scroll to **Integrate calendar**
4. Copy **Calendar ID**
   - Primary personal calendar is usually your Gmail address, e.g. `you@gmail.com`
   - Shared/family calendars often look like `xxxxxxxx@group.calendar.google.com`
   - Some look like `xxxxxxxx@gmail.com` or a long hex id

Map them in `.env` like this:

| Jarvis env var | Which calendar |
|----------------|----------------|
| `GOOGLE_CALENDAR_FAMILY_ID` | Family shared calendar |
| `GOOGLE_CALENDAR_SPOUSE_ID` | Spouse personal calendar (must be visible to the connected Google account) |
| `GOOGLE_CALENDAR_WORK_ID` | Work calendar (read-only in Jarvis) |

Example:

```bash
GOOGLE_CALENDAR_FAMILY_ID=abcd1234@group.calendar.google.com
GOOGLE_CALENDAR_SPOUSE_ID=spouse@gmail.com
GOOGLE_CALENDAR_WORK_ID=work-user@company.com
```

Notes:
- The connected OAuth account must already have access to each calendar (shared with it, or owned by it)
- You can leave `GOOGLE_CALENDAR_WORK_ID` blank if you are not wiring work yet
- After editing `.env`:

```bash
docker compose up -d connectors-google
```

#### Find Google Tasks list IDs (optional)
Task list IDs are harder to see in the UI. Safe approach for now:
1. Leave `GOOGLE_TASKS_LIST_*` blank
2. Jarvis will look up lists by title (`household`, `shopping`, `personal`) and create them if missing

If you want exact IDs later, use Google Tasks in the API Explorer or a quick API call with the connected token.
### B9. Google troubleshooting

| What you see | Fix |
|--------------|-----|
| `OAuth start failed` / 503 | `.env` missing client id/secret, or connectors not restarted |
| `redirect_uri_mismatch` | Redirect URI in Cloud Console must **exactly** match `GOOGLE_REDIRECT_URI` |
| `access_denied` / app not verified | Add your Google account under OAuth consent **Test users** |
| `"connected": false` after approve | Check `docker compose logs connectors-google` and callback URL |
| `502` / `token endpoint unreachable` / DNS failure | `connectors-google` must be on the Compose `egress` network (outbound Google APIs). Run `docker compose up -d connectors-google scheduler` after pulling the fix. Authorization codes are one-time — restart from `/oauth/google/start` |
| `Google token exchange rejected (400)` | Wrong client secret, reused code, or redirect URI mismatch |

---

## Part C — Public HTTPS URL for Alexa

Amazon will not call `http://localhost:8002`. You need a public `https://.../alexa/webhook`.

Pick **one** option.

### Option C-ngrok (fastest for first test)

#### C1. Install ngrok
```bash
brew install ngrok
```

#### C2. Create a free ngrok account (required on current ngrok)
1. Go to https://dashboard.ngrok.com/signup
2. Sign up
3. Open https://dashboard.ngrok.com/get-started/your-authtoken
4. Copy your authtoken
5. Run:

```bash
ngrok config add-authtoken PASTE_TOKEN_HERE
```

#### C3. Start the tunnel (leave this terminal open)
```bash
ngrok http 8002
```

#### C4. Copy the HTTPS URL
In the ngrok UI / terminal you will see something like:

```text
Forwarding  https://abc123.ngrok-free.app -> http://localhost:8002
```

Your Alexa endpoint will be:

```text
https://abc123.ngrok-free.app/alexa/webhook
```

Notes:
- Free ngrok URLs change every time you restart ngrok unless you pay for a reserved domain
- If Alexa console still has an old URL, update it after every ngrok restart
- Keep Jarvis `JARVIS_ALEXA_SKIP_VERIFY=0` (already set)

#### C5. Quick public health check
From another device or using curl:

```bash
curl -s https://abc123.ngrok-free.app/health
```

Should return `{"status":"ok"}`.  
If ngrok shows an interstitial browser page for humans, API/Alexa POSTs still usually work; if not, use Cloudflare instead.

### Option C-Cloudflare Tunnel (better long-term)

Use this if you own a domain on Cloudflare.

#### C1. Install + login
```bash
brew install cloudflared
cloudflared tunnel login
```

A browser window opens → choose your Cloudflare account/domain → Authorize.

#### C2. Create tunnel + DNS
```bash
cloudflared tunnel create jarvis-alexa
cloudflared tunnel route dns jarvis-alexa alexa.YOUR_DOMAIN.com
```

Replace `YOUR_DOMAIN.com` with a domain you control in Cloudflare DNS.

#### C3. Run the tunnel
```bash
cloudflared tunnel run --url http://localhost:8002 jarvis-alexa
```

Endpoint:

```text
https://alexa.YOUR_DOMAIN.com/alexa/webhook
```

#### C4. Health check
```bash
curl -s https://alexa.YOUR_DOMAIN.com/health
```

---

## Part D — Create the Alexa Custom Skill

Use the **same Amazon account** that owns the Echo/Show devices (or an account in the household Amazon Household).

### D1. Open Alexa Developer Console
1. Go to https://developer.amazon.com/alexa/console/ask
2. Sign in
3. If prompted to complete developer profile (name, address), finish that first

### D2. Create the skill
1. Click **Create Skill**
2. Skill name: `Jarvis`
3. Primary locale: **English (US)** (or your locale)
4. Click **Next**
5. Experience type: **Other** → **Custom**
6. Hosting: **Provision your own**
7. Click **Create skill** / **Next** as prompted until you reach the skill Build page

(UI labels vary slightly; the important choices are **Custom** + **Provision your own**.)

### D3. Set invocation name
1. Left menu → **Invocations** → **Skill Invocation Name**
2. Set to: `jarvis bot`
3. Save

Utterances will sound like: “Alexa, ask **jarvis bot** what’s on today.”

### D4. Paste the interaction model
1. Left menu → **Interaction Model** → **JSON Editor**
2. On your Mac, open this file and copy **all** of it:

```text
~/Projects/jarvis/docs/alexa-interaction-model.json
```

3. In the Alexa JSON Editor, select everything → paste the file contents
4. Click **Save Model**
5. Click **Build Model**
6. Wait until build succeeds (green / “Build Successful”)

If build fails on a slot type or sample, re-copy the JSON carefully (must be valid JSON).

Note: `title` uses a custom `TITLE` slot (not `AMAZON.SearchQuery`) because Alexa forbids mixing a phrase/search slot with other slots in the same sample utterance.

### D5. Configure the HTTPS endpoint
1. Left menu → **Endpoint**
2. Service endpoint type: **HTTPS**
3. Default region box: paste your public webhook **including the path**:

```text
https://YOUR_SUBDOMAIN.ngrok-free.dev/alexa/webhook
```

It must end with `/alexa/webhook`. A bare host (`https://….ngrok-free.dev`) returns 404 and Alexa says it cannot reach the skill.

Quick checks:
- `curl -s https://…/alexa/webhook` (GET) should return JSON saying the webhook is up (not 404).
- Alexa never uses GET. A real Test-tab utterance must create a `POST /alexa/webhook` line in `docker compose logs -f alexa-ingress`.
- If Test says unreachable but logs show **no POST**, the skill Endpoint URL/region was not saved correctly — fix Endpoint, Save, retry.

4. SSL certificate type — this must match the cert ngrok actually serves:
   - **ngrok** (`*.ngrok-free.dev`): choose  
     **My development endpoint is a sub-domain of a domain that has a wildcard certificate from a certificate authority**
   - Cloudflare / dedicated host cert: choose  
     **My development endpoint has a certificate from a trusted certificate authority**
   - Wrong SSL type → Alexa never POSTs and says “unable to reach the requested skill” (ngrok/Jarvis look fine from curl).
5. Leave **optional regional endpoints** (North America, Europe, etc.) **empty** unless they are different URLs. Duplicating the same URL in Default + NA has broken reachability for some skills.
6. Click **Save Endpoints**

### D6. Enable testing
1. Top tabs → **Test**
2. Skill testing enabled in: change dropdown from **Off** to **Development**
3. In the utterance box, type (do **not** include the wake word “Alexa” here):

```text
ask jarvis bot what's on today
```

4. Press enter

#### Expected good result
- Right-side panels show request/response JSON
- Skill speech text is a normal Jarvis reply (schedule summary, confirmation prompt, etc.)
- On the Mac:

```bash
docker compose logs --tail=50 alexa-ingress core
```

You should see request logs.

#### If Test tab fails
| Symptom | Check |
|---------|-------|
| “Sorry, I couldn't understand…” | Interaction model not built / wrong invocation |
| “The skill returned an invalid response” / endpoint error | Public URL wrong, tunnel down, missing `/alexa/webhook` |
| HTTP 401 in Jarvis logs | Signature verification problem; confirm tunnel forwards full body/headers |
| Timeout | Jarvis containers down; `docker compose ps` |

### D7. Test on a real Echo / Show
1. Make sure the Echo is on the same Amazon account used in the developer console
2. Wait 1–2 minutes after enabling Development testing
3. Say:

> “Alexa, ask jarvis bot what’s on today.”

Other phrases:

> “Alexa, ask jarvis bot to add event Soccer practice”  
> “Alexa, ask jarvis bot to add task Buy milk”  
> “Alexa, ask jarvis bot to set a timer for 10 minutes”

4. For calendar writes / some tasks, Jarvis will ask for confirmation
5. On the Mac Mini (or VPN phone): open http://localhost:8006/
6. In **Pending approvals**, enter household PIN (default in `.env` is `1234` unless you changed it) → **Approve**

---

## Part E — End-to-end smoke checklist

Run this once Google + tunnel + skill are configured.

1. [ ] `curl -s http://localhost:8002/health` → ok  
2. [ ] `curl -s http://localhost:8006/oauth/google/status` → `"connected": true`  
3. [ ] `curl -s https://YOUR_PUBLIC_HOST/health` → ok  
4. [ ] Alexa console Test: `ask jarvis bot what's on today` → spoken reply  
5. [ ] Echo device: same phrase → spoken reply  
6. [ ] Echo: add event → appears under Pending approvals on dashboard  
7. [ ] Approve with PIN → event created  
8. [ ] Logs look clean:

```bash
cd ~/Projects/jarvis
docker compose logs -f alexa-ingress core connectors-google web
```

---

## Part F — Useful reference

### Environment keys that matter

```bash
JARVIS_HOUSEHOLD_PIN=1234
JARVIS_ALEXA_SKIP_VERIFY=0
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8006/oauth/google/callback
```

If your Postgres password contains `$`, write it as `$$` in `.env` so Docker Compose does not treat it as a variable.

### Intent map

| Alexa intent | Jarvis intent | Slots |
|--------------|---------------|-------|
| `WhatTodayIntent` | `what_today` | `date` |
| `AddEventIntent` | `add_event` | `title`, `date`, `time`, `calendar_scope` |
| `AddTaskIntent` | `add_task` | `title`, `list`, `date` |
| `SetTimerIntent` | `set_timer` | `duration` (Alexa `PT10M` → seconds) |

### Files
- Interaction model: `docs/alexa-interaction-model.json`
- This runbook: `docs/ops-alexa-setup.md`
- Dashboard: http://localhost:8006/
- Local ingress health: http://localhost:8002/health
- Google connect: http://localhost:8006/oauth/google/start
- Google status: http://localhost:8006/oauth/google/status

---

## Suggested working order today

1. Finish **Part A** (confirm stack healthy)  
2. Finish **Part B** (Google OAuth → `"connected": true`)  
3. Start **Part C** with **ngrok** for the first test  
4. Finish **Part D** (skill + endpoint + Test tab)  
5. Run **Part E** on Echo  

When Google credentials are in `.env`, tell me and I can verify connect/status and help with the tunnel + skill smoke test from here.
