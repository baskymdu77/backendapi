@echo off
echo Loading environment variables from .env file...
for /f "tokens=*" %%a in (.env) do (
    set %%a
)

echo Starting FastAPI server...
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
