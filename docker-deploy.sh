#!/bin/bash
set -euo pipefail  
set -x  # Print all commands (for debug trace)

echo "📁 Navigating to project directory..."
cd /home/azureuser/construction_manage_api  # Change this to your actual path

echo "🛠 Building Docker image..."
docker build -t homeapi .

echo "🧼 Stopping and removing existing container (if any)..."
docker stop backend || true
docker rm backend || true

echo "🚀 Running new container..."
docker run -d --name backend -p 8001:8001 homeapi

echo "✅ Deployment complete."
