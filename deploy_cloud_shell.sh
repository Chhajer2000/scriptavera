#!/bin/bash
set -e

echo "=== Scriptavera Cloud Run Deployment ==="

# Switch to the correct Google Cloud Project for Scriptavera
echo "Setting project to gen-lang-client-0978702057..."
gcloud config set project gen-lang-client-0978702057

# Check if scriptavera directory exists in home
if [ -d "$HOME/scriptavera" ]; then
    echo "Entering existing scriptavera directory..."
    cd "$HOME/scriptavera"
    echo "Pulling latest code from GitHub main branch..."
    git pull origin main
else
    echo "Cloning scriptavera repository..."
    cd "$HOME"
    git clone https://github.com/Chhajer2000/scriptavera.git
    cd scriptavera
fi

echo "Deploying to Google Cloud Run in region asia-south1 on project gen-lang-client-0978702057..."
gcloud run deploy scriptavera --source . --region asia-south1 --allow-unauthenticated --project gen-lang-client-0978702057

echo "=== Deployment Complete! ==="
