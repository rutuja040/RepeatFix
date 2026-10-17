# RepeatFix - Backend

Python FastAPI backend for RepeatFix.

## Setup (Windows PowerShell)

```powershell
# Create virtualenv (optional but recommended)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install deps
pip install -r backend/requirements.txt

# Run backend
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

API: http://localhost:8000
Docs: http://localhost:8000/docs
Health: http://localhost:8000/health (or /api/v1/health)
