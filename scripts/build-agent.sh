#!/bin/bash
# Build the Nanoclaw agent Docker image

set -e

echo "Building Nanoclaw agent container..."

cd "$(dirname "$0")/.."

# Build Docker image
docker build \
  -t nanoclaw-agent:latest \
  -f agent/Dockerfile.yaml \
  agent

echo "✅ Agent container built successfully!"
echo "Image: nanoclaw-agent:latest"
