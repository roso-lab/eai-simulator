#!/bin/bash
#
# Setup script for Git hooks
# Run this script after cloning the repository to enable:
#   - Commit message validation (#IID format)
#   - Branch naming convention enforcement
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================="
echo "  Setting up Git hooks for eai-simulator"
echo "========================================="
echo ""

# Move to repository root before touching git config or hooks.
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_ROOT"

# Configure git to use .githooks directory
echo "📁 Configuring Git to use .githooks directory..."
git config core.hooksPath .githooks

# Make hooks executable
echo "🔐 Making hooks executable..."
chmod +x .githooks/*

echo ""
echo "========================================="
echo "  ✅ Git hooks setup complete!"
echo "========================================="
echo ""
echo "📝 Commit message format:"
echo "   #IID <description>"
echo "   Example: git commit -m '#123 Add new feature'"
echo ""
echo "🌿 Branch naming convention:"
echo "   main, develop"
echo "   feature/<name>, bugfix/<name>, hotfix/<name>"
echo "   chore/<name>, docs/<name>, release/<name>"
