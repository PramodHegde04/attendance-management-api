# Attendance Management API

A REST API for employee management, attendance tracking, manual attendance corrections, and workforce analytics, built with FastAPI and MongoDB.

## Features

- Employee creation, listing, and filtering
- Attendance punch-in and punch-out
- Attendance history and manual corrections
- Monthly employee and department analytics
- Late-arrival leaderboard and daily department trends
- MongoDB indexes and pagination

## Tech Stack

- Python 3.12+
- FastAPI
- MongoDB 7.0+
- PyMongo
- Uvicorn

## Prerequisites

Install Python and MongoDB before running the application.

## Local Setup (Windows PowerShell)

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Create a `.env` file in the project root with your local MongoDB settings:

```env
MONGO_URI=mongodb://127.0.0.1:27017
MONGO_DB=attendance_db
```

Start the API:

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

Open the interactive API documentation at http://127.0.0.1:8000/docs.

## Project Files

- `app/main.py` — API endpoints, validation, attendance rules, and analytics.
- `openapi.yaml` — API contract and business requirements.
- `DATA_MODEL.md` — MongoDB data model.
- `sample_data/` — Sample data fixtures.
- `sample_seed.py` — Loads sample data into MongoDB.
- `REVIEW.md` — Code review findings and testing notes.
- `DECISIONS.md` — Design decisions and trade-offs.
- `requirements.txt` — Python dependencies.

## Notes

- Keep `.env` and credentials private. Never commit secrets.
- The sample seeding script resets sample collections, so use it carefully.
- The API is intended to be run locally for evaluation unless deployed separately.