#!/usr/bin/env bash
# AgentForge — Local Development & Execution Script
set -e

echo "=== AgentForge Local Runner ==="

# Check Python environment
if ! command -v python &> /dev/null; then
    echo "Error: Python 3 is not installed or not on PATH."
    exit 1
fi

# 1. Run unit and integration tests
echo "Running full test suite..."
python -m pytest tests -v

echo ""
echo "=== Test Suite Passed! ==="
echo ""
echo "To run agents locally in standalone mode:"
echo "  1. Builder Agent:    python agents/builder_agent.py"
echo "  2. Extractor Agent:  python agents/extractor_agent.py"
echo "  3. Storefront Hub:   python agents/storefront_agent.py"
echo ""
echo "To generate single-file bundles for Agentverse hosted agents:"
echo "  python scripts/bundle_for_agentverse.py"
echo ""
