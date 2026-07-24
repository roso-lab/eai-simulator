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
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# Configure git to use .githooks directory
echo "📁 Configuring Git to use .githooks directory..."
git config core.hooksPath .githooks

# Make hooks executable
echo "🔐 Making hooks executable..."
chmod +x .githooks/*

# Install pre-commit hooks (if pre-commit is available)
if command -v pre-commit &> /dev/null; then
    echo "🔧 Installing pre-commit hooks..."
    pre-commit install
    pre-commit install --hook-type commit-msg
    pre-commit install --hook-type pre-push
    echo "✅ pre-commit hooks installed successfully!"
else
    echo "⚠️  pre-commit not found. Install it with: pip install pre-commit"
    echo "   Then run:"
    echo "     pre-commit install"
    echo "     pre-commit install --hook-type commit-msg"
    echo "     pre-commit install --hook-type pre-push"
fi

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
