#!/bin/bash
# Deploys Scriptavera to Cloud Run. Holds no secrets: the Supabase key and the sign-in secret live in
# Secret Manager (sv-supabase-key, sv-session-secret). --update-env-vars keeps every other setting on the service;
# never use --set-env-vars here, it deletes all settings that are not listed.
set -e

echo "=== Scriptavera Cloud Run Deployment ==="
gcloud config set project gen-lang-client-0978702057

if [ -d "$HOME/scriptavera" ]; then
    cd "$HOME/scriptavera"
    git pull origin main
else
    cd "$HOME"
    git clone https://github.com/Chhajer2000/scriptavera.git
    cd scriptavera
fi

gcloud run deploy scriptavera --quiet \
  --source . \
  --region asia-south1 \
  --project gen-lang-client-0978702057 \
  --allow-unauthenticated \
  --session-affinity \
  --max-instances 1 --min-instances 1 \
  --no-cpu-throttling --cpu 2 --memory 4Gi --timeout 3600 \
  --update-env-vars "SCRIPTAVERA_GCS_BUCKET=scriptaveradata,SCRIPTAVERA_ADMIN_EMAILS=vchhajer0504@gmail.com,GOOGLE_CLIENT_ID=991836161184-4u1qem087jivf3pjom9na4q0phqqmf1o.apps.googleusercontent.com" \
  --update-secrets "SUPABASE_KEY=sv-supabase-key:latest,SCRIPTAVERA_SESSION_SECRET=sv-session-secret:latest"

echo "=== Deployment Complete! ==="
