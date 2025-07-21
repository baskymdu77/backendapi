#!/bin/bash
echo "📁 Navigating to project directory..."
cd /home/azureuser/construction_manage_api

echo "🛠 Building Docker image..."
docker build -t homeapi .  2>&1 | grep -v '^\+' | tee docker_build_clean.log

echo "🧼 Stopping and removing existing container (if any)..."
docker stop backend || true
docker rm backend || true

echo "🚀 Running new container..."
docker run -d --name backend -p 8001:8001 homeapi

echo "✅ Deployment complete."
