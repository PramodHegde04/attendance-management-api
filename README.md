# Attendance Management API

A REST API for employee management, attendance tracking, manual attendance corrections, and workforce analytics, built with FastAPI and MongoDB.

## Features

- Employee creation, listing, and filtering
- Attendance punch-in and punch-out with validation
- Attendance history and manual corrections with audit trails
- Monthly employee and department analytics
- Late-arrival leaderboard and daily department attendance trends
- MongoDB indexes and paginated responses
- Query-plan inspection for selected analytics and attendance endpoints

## Tech Stack

- Python 3.12+
- FastAPI
- MongoDB 7.0+
- PyMongo
- Uvicorn

## Prerequisites

Install Python 3.12 or later and MongoDB before running the application.

## Local Setup (Windows PowerShell)

### 1. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the project root with your local MongoDB settings:

```env
MONGO_URI=mongodb://127.0.0.1:27017
MONGO_DB=attendance_db
```

Keep `.env` private and do not commit it to GitHub.

### 4. Load sample data (optional)

If you want to initialize the database with the provided sample data, run:

```powershell
python sample_seed.py
```

**Warning:** The seeding script resets the relevant sample collections. Run it only when you intend to reset the sample data.

### 5. Start the API

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

Open the interactive API documentation at:

http://127.0.0.1:8000/docs

## Project Structure

- `app/main.py` — API endpoints, validation, attendance rules, and analytics.
- `openapi.yaml` — API contract and business requirements.
- `DATA_MODEL.md` — MongoDB data model.
- `sample_data/` — Sample data fixtures.
- `sample_seed.py` — Loads sample data into MongoDB.
- `REVIEW.md` — Code review findings and testing notes.
- `DECISIONS.md` — Design decisions and trade-offs.
- `requirements.txt` — Python dependencies.

## Notes

- MongoDB must be running before starting the API.
- Keep `.env` files, credentials, and other secrets private.
- The sample seeding script resets sample collections, so use it carefully.
- The API is intended to run locally for evaluation; deployment requires separate hosting and configuration.
