# Deploying scriptavera.com on Google Cloud Run

The same code runs as the desktop app (`python app.py` on a computer) and as the hosted service. The Dockerfile
sets `SCRIPTAVERA_PUBLIC=1`, which turns on everything below; without it nothing changes for desktop users.

## What the hosted service does differently

| | Desktop app | scriptavera.com |
|---|---|---|
| Listens on | 127.0.0.1, port 8765 | 0.0.0.0, the port in `PORT` (8080) |
| Host names | this computer | scriptavera.com, www (redirected), `SCRIPTAVERA_ALLOWED_HOSTS`; scriptavera.org and www.scriptavera.org are redirected (301) to https://scriptavera.com |
| Papers | everything on the computer | each person sees only their own (account, or this browser as a guest) |
| Settings, AI keys, test mode | the user | only `SCRIPTAVERA_ADMIN_EMAILS` |
| Free plan | none | guests: 3 papers per browser, and at most `SCRIPTAVERA_GUEST_NETWORK_LIMIT` (25) per network per month (403 after that); 3 per account in all, then a subscription (402) |
| AI keys | `.env` on the computer | each person's own, kept in their browser; the server holds them in memory only, never on disk or in the database (`SCRIPTAVERA_SHARED_KEYS=1` lets people without keys use the service's) |
| What is learned about notation and handwriting | `memory/` on the computer | per person: their own row in `user_memory` (guests: server memory only) |
| Signed records (RFC 3161, OpenTimestamps) | always | only for people signed in with Google Drive connected; copied to `gs://BUCKET/signatures/<paper>/` |
| Phone upload over Wi-Fi | on | off |

## The one-instance rule

A paper is made by background threads inside the server process, and its files live in the instance's memory
(Cloud Run's file system is in memory). Run **exactly one instance with CPU always allocated**:

* `--max-instances 1 --min-instances 1`: a second instance would not see the first one's papers.
* `--no-cpu-throttling`: otherwise Cloud Run freezes the CPU between requests and papers stop half way.
* Memory: about 1 GB plus 50 MB per paper in progress; `--memory 4Gi` is a good start.

Signed-in people's work is checkpointed to Cloud Storage every 30 seconds and restored after a restart. Guests'
work is not (they are told so), and a guest paper nobody comes back to is deleted after 72 hours.

## 1. Google Cloud project

```bash
gcloud config set project YOUR_PROJECT
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
    secretmanager.googleapis.com storage.googleapis.com drive.googleapis.com
REGION=asia-south1            # Mumbai; see "Domain" below
BUCKET=scriptavera-data       # any free name
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access --public-access-prevention
gcloud iam service-accounts create scriptavera-run
SA=scriptavera-run@YOUR_PROJECT.iam.gserviceaccount.com
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$SA --role=roles/storage.objectAdmin
```

For a ledger nobody can alter, put `signatures/` in a bucket of its own with a locked retention policy
(Bucket Lock) and set `SCRIPTAVERA_GCS_BUCKET` accordingly; a retention policy applies to a whole bucket.

## 2. Supabase

1. Create a project at supabase.com (region Mumbai, ap-south-1).
2. SQL editor: paste and run `supabase/schema.sql` (safe to run again after updates).
3. Project settings, API: copy the URL and the **service_role** key. Never the anon key: every table has row level
   security with no policy, so the anon key reads nothing, and the service_role key must never reach a browser.

## 3. Google sign-in and Drive

Google Cloud console, APIs and services:

1. OAuth consent screen: External; app name Scriptavera; domain scriptavera.com; privacy policy and terms URLs.
   Scopes: `openid`, `email`, `profile`, `https://www.googleapis.com/auth/drive.file`. drive.file is a
   non-sensitive scope (Scriptavera sees only files it creates), so no security assessment is needed; publish the
   app to production and complete brand verification.
2. Credentials, Create OAuth client ID, Web application. Authorized JavaScript origins:
   `https://scriptavera.com` (and `http://localhost:8080` for testing). No redirect URI is needed (popup mode).
3. The client ID goes in `GOOGLE_CLIENT_ID`. There is no client secret to keep: the server checks Google's
   signature on each sign-in.

## 4. Secrets

```bash
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create sv-session-secret --data-file=-
printf '%s' "SUPABASE_SERVICE_ROLE_KEY" | gcloud secrets create sv-supabase-key --data-file=-
printf '%s' "PAID_TIER_GEMINI_KEY"      | gcloud secrets create sv-gemini-key --data-file=-
openssl genpkey -algorithm ed25519 -out service-signing-key.pem      # the service's own key, not your personal one
gcloud secrets create sv-signing-key --data-file=service-signing-key.pem && shred -u service-signing-key.pem
# subscriptions (Razorpay): key id, key secret, webhook secret
for s in sv-razorpay-key-id sv-razorpay-key-secret sv-razorpay-webhook; do printf 'VALUE' | gcloud secrets create $s --data-file=-; done
for s in sv-session-secret sv-supabase-key sv-gemini-key sv-signing-key sv-razorpay-key-id sv-razorpay-key-secret sv-razorpay-webhook; do
  gcloud secrets add-iam-policy-binding $s --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor; done
```

People bring their own AI keys (the "AI keys" button); the service's own key is used only by the administrators,
or by everyone when `SCRIPTAVERA_SHARED_KEYS=1`. If you share it, use a **paid-tier** key. With a free-tier Gemini key Google may use what is sent to improve its
products and people may review it; the upload page warns users whenever a free-tier key is in use.

`SCRIPTAVERA_SESSION_SECRET` signs the cookies and keys the hash of visitors' addresses: keep it stable, or every
session ends and the guest ledger starts again.

## 5. Deploy

```bash
gcloud run deploy scriptavera --source . --region $REGION --service-account $SA --allow-unauthenticated \
  --max-instances 1 --min-instances 1 --no-cpu-throttling --cpu 2 --memory 4Gi --timeout 3600 --concurrency 80 \
  --set-env-vars "SCRIPTAVERA_GCS_BUCKET=$BUCKET,SUPABASE_URL=https://XXXX.supabase.co,GOOGLE_CLIENT_ID=XXXX.apps.googleusercontent.com,SCRIPTAVERA_ADMIN_EMAILS=you@example.com,SCRIPTAVERA_ALLOWED_HOSTS=.run.app,SCRIPTAVERA_PROXY_HOPS=2,SCRIPTAVERA_PRICE_LABEL=₹499 per month,RAZORPAY_PLAN_ID=plan_XXXX,CONTACT_EMAIL=you@example.com" \
  --set-secrets "SCRIPTAVERA_SESSION_SECRET=sv-session-secret:latest,SUPABASE_KEY=sv-supabase-key:latest,GEMINI_API_KEY=sv-gemini-key:latest,SCRIPTAVERA_SIGNING_KEY_PEM=sv-signing-key:latest,RAZORPAY_KEY_ID=sv-razorpay-key-id:latest,RAZORPAY_KEY_SECRET=sv-razorpay-key-secret:latest,RAZORPAY_WEBHOOK_SECRET=sv-razorpay-webhook:latest"
```

## 6. Domain (scriptavera.com, and scriptavera.org redirected to it)

Cloud Run's own domain mapping exists only in some regions (not asia-south1). Two ways:

* **Mumbai (asia-south1), recommended for users in India:** a global external Application Load Balancer with a
  serverless network endpoint group pointing at the service, and one Google-managed certificate for
  `scriptavera.com, www.scriptavera.com, scriptavera.org, www.scriptavera.org`. Point all four names' A/AAAA
  records at the load balancer's address. Set `SCRIPTAVERA_PROXY_HOPS=2` (the load balancer adds two addresses to
  X-Forwarded-For). The app itself answers www and both .org names with a 301 to https://scriptavera.com.
* **A region with domain mapping (e.g. asia-southeast1):** `gcloud beta run domain-mappings create --service
  scriptavera --domain scriptavera.com` (and the others), set the DNS records it prints, and use
  `SCRIPTAVERA_PROXY_HOPS=1`.

Either way you can instead forward the .org domain at your registrar (a 301 to https://scriptavera.com); the app's
own redirect then never sees it.

## 7. Check it

Sign in with an e-mail listed in `SCRIPTAVERA_ADMIN_EMAILS` and open `https://scriptavera.com/api/admin/whoami`:
`ip` must be your own public address (compare with any "what is my IP" page), `database` must be `supabase`,
`storage` and `session_secret` must be `true`. If `ip` is the load balancer's address, `SCRIPTAVERA_PROXY_HOPS`
is wrong. Never trust the first X-Forwarded-For entry: the visitor writes it.

## 8. Subscriptions (Razorpay)

1. Razorpay dashboard: Subscriptions, create a monthly plan; its id goes in `RAZORPAY_PLAN_ID`.
2. Webhooks: URL `https://scriptavera.com/api/billing/webhook`, a secret (`RAZORPAY_WEBHOOK_SECRET`), events
   `subscription.authenticated, activated, charged, pending, halted, paused, resumed, cancelled, completed, expired`.
3. "Subscribe" creates a subscription carrying the account id and sends the person to Razorpay's page; the
   webhook turns the subscription on. Without Razorpay keys, `SCRIPTAVERA_CHECKOUT_URL` can point anywhere.

## Environment variables

| Variable | Meaning |
|---|---|
| `PORT` | Set by Cloud Run (8080 in the Dockerfile) |
| `SCRIPTAVERA_PUBLIC` | 1 = hosted service (set in the Dockerfile; Cloud Run's `K_SERVICE` also turns it on) |
| `SCRIPTAVERA_ALLOWED_HOSTS` | Extra host names, comma separated; `.run.app` stands for every name ending so |
| `SCRIPTAVERA_REDIRECT_HOSTS` | Extra names redirected to the canonical one |
| `SCRIPTAVERA_CANONICAL_HOST` | Default scriptavera.com |
| `SCRIPTAVERA_PROXY_HOPS` | Which X-Forwarded-For entry is the visitor, from the right: 1 direct, 2 behind a load balancer |
| `SCRIPTAVERA_SESSION_SECRET` | Required. Signs cookies; keys the hashed addresses |
| `SCRIPTAVERA_ADMIN_EMAILS` | Who may open Settings and change the service's keys |
| `GOOGLE_CLIENT_ID` | The OAuth web client |
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase; the service_role key. Without them: SQLite in memory (development only) |
| `SCRIPTAVERA_GCS_BUCKET` | Cloud Storage bucket for signatures, progress and the opt-in test suite |
| `SCRIPTAVERA_SIGNING_KEY_PEM` | The service's Ed25519 signing key (PEM) |
| `SCRIPTAVERA_FREE_PAPERS` | Default 3 (per guest browser, and per account) |
| `SCRIPTAVERA_GUEST_NETWORK_LIMIT` | Guest papers one network may start per 30 days, however many people share it (default 25) |
| `SCRIPTAVERA_SHARED_KEYS` | 1 = people who add no key of their own use the service's keys (default: everyone brings their own) |
| `SCRIPTAVERA_PRICE_LABEL` | Shown with "Subscribe", e.g. ₹499 per month |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_PLAN_ID`, `RAZORPAY_WEBHOOK_SECRET` | Subscriptions |
| `SCRIPTAVERA_CHECKOUT_URL` | A payment page, when Razorpay keys are not set |
| `SCRIPTAVERA_PURGE_GRACE_MINUTES` | After a finished paper is downloaded, how long before the notes are purged (default 15) |
| `SCRIPTAVERA_GUEST_TTL_HOURS` | When an untouched guest paper is deleted (default 72) |
| `SCRIPTAVERA_SYNC_SECONDS` | How often progress is checkpointed (default 30) |
| `GEMINI_API_KEY` and the other keys in `.env` | The service's AI keys (use paid tiers) |
