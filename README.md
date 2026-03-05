# AI-Assisted Faculty Attendance System

A full-stack web application designed to help university faculty seamlessly manage their class schedules and automate student attendance. 

This project currently features a fully functional Faculty Portal (themed after Liwa University) that reads institutional data, authenticates faculty members, and organizes their academic schedules and student lists.

## Phase 1 Features (Completed)
* **Custom UI/UX Theme:** A fully responsive, modern landing page matching the Liwa University brand guidelines (Hero Section, Statistics, About, Services, and Partners Carousel).
* **Faculty Authentication:** Secure login using faculty email addresses cross-referenced against the university database.
* **Dynamic Dashboard:** Once logged in, faculty can view a unique list of all assigned classes, including schedules, course codes, and room numbers.
* **Student Registers:** Clicking on any class dynamically fetches and displays the enrolled student list for that specific course, preparing the UI for the upcoming AI attendance verification.
* **Automated Data Handling:** Backend seamlessly reads and processes complex, repetitive Excel (`.xlsx`) academic records into clean JSON APIs.

## Tech Stack
**Backend:**
* Python 3.10+
* FastAPI (High-performance API framework)
* Pandas & OpenPyXL (Data extraction and manipulation)
* Uvicorn (ASGI web server)

**Frontend:**
* React.js (Bootstrapped with Vite for speed)
* Pure CSS (Custom theming, Flexbox, CSS Grid)

# PYTHON LIBRARIES 
 pip install fastapi uvicorn pandas openpyxl pydantic
# Start the FastAPI server
python main.py
## Project Structure
```text
AI_attendance_app/
│
├── data/
│   └── data.xlsx          # Institutional database (Faculty, Classes, Students)
│
├── frontend/              # React Application
│   ├── src/
│   │   ├── App.jsx        # Main React Logic & UI Components
│   │   ├── App.css        # Custom Liwa University Styling
│   │   └── main.jsx       # React DOM rendering
│   └── package.json       # Frontend dependencies
│
├── main.py                # FastAPI Backend Logic
└── README.md              # Project Documentation
