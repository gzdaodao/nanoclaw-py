#!/bin/bash
# Build the Nanoclaw Docker image

set -e

echo "Building Nanoclaw container..."

cd "$(dirname "$0")/.."

# Build Docker image
docker build \
  -t nanoclaw:latest \
  -f Dockerfile.yaml \
  ./

echo "✅ Agent container built successfully!"
echo "Image: nanoclaw-agent:latest"
