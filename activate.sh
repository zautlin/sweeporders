#!/bin/bash
# Activate virtual environment for sweeporders project

echo "Activating sweeporders virtual environment..."
source swp_env/bin/activate

echo "✅ Environment activated"
echo ""
echo "Python version: $(python --version)"
echo "Python location: $(which python)"
echo ""
echo "Installed packages:"
pip list | grep -E "pandas|numpy|scipy|psutil" | column -t
echo ""
echo "To run the pipeline:"
echo "  cd src"
echo "  python main.py"
echo ""
echo "To deactivate:"
echo "  deactivate"
