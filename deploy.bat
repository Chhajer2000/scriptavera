@echo off
REM SPDX-License-Identifier: AGPL-3.0-only
REM Copyright (C) 2026 Vaibhav Chhajer
echo Pulling latest main branch from GitHub...
git pull origin main
echo Deploying scriptavera to Google Cloud Run...
gcloud run deploy scriptavera --source . --region asia-south1
pause
