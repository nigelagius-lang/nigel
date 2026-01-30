#!/bin/bash
# Run Meta Marketing Dashboard

cd "$(dirname "$0")"

# Check if setup has been run
if [ ! -f ".env" ]; then
    echo "Running initial setup..."
    python setup.py
fi

# Check if streamlit is installed
if ! command -v streamlit &> /dev/null; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
fi

# Run the dashboard
echo "Starting Meta Marketing Dashboard..."
streamlit run app.py --server.headless true
