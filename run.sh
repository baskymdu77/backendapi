#!/bin/bash

# Check if .env file exists and load it
if [ -f .env ]; then
    echo "Loading environment variables from .env file"
    export $(grep -v '^#' .env | xargs)
else
    echo "Warning: .env file not found. Make sure to set OPENAI_API_KEY manually."
fi

# Run the FastAPI application
uvicorn app.main:app --reload --port 8001