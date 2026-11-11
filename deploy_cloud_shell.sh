#!/bin/bash
set -e

echo "=== Scriptavera Cloud Run Deployment ==="

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

echo "Deploying to Google Cloud Run in region asia-south1..."
gcloud run deploy scriptavera --source . --region asia-south1 --allow-unauthenticated

echo "=== Deployment Complete! ==="
