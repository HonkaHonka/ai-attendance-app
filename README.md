🎓 AI-Powered Smart Classroom Attendance System
An Edge-AI attendance tracking system designed for Smart Classroom TVs. It features real-time facial recognition using a Two-Stage Vision Pipeline, QR-code-based teacher authentication, and a Polyglot Persistence Architecture splitting relational data and AI vectors.
🏗️ System Architecture & Tech Stack
Frontend: React, HTML5 Canvas, WebSockets (Compiled statically via Vite).
Backend: Python, FastAPI, Uvicorn.
AI Pipeline: YOLOv8n (OpenVINO / GPU), InsightFace buffalo_s (MobileFaceNet / CPU).
Databases:
Microsoft SQL Server: Master records (Rosters, Students, Logs).
PostgreSQL + pgvector: AI Microservice (512D Face Embeddings).
🛠️ PHASE 1: System Installations (Prerequisites)
Before configuring the code, the following core technologies must be downloaded and installed on the target Windows 11 machine:
1. Programming Languages
Python 3.10 or higher: Ensure the "Add Python to PATH" checkbox is selected during installation.
Node.js (LTS version): Required to install npm and build the React frontend.
2. C++ Compilers (Required for AI Vector Database)
Download Visual Studio Build Tools 2022.
Run the installer and check the box for "Desktop development with C++". (This installs the nmake tool required to compile the pgvector extension from source on Windows).
3. Databases
Microsoft SQL Server Express 2022: Install the basic engine.
SQL Server Management Studio (SSMS) 19+: The graphical interface for MS SQL.
PostgreSQL (15, 16, or 17): Ensure Command Line Tools and pgAdmin 4 are checked during installation. Remember the postgres user password!
🗄️ PHASE 2: Database Setup
A. MS SQL Server (Rosters & Logs)
Open SSMS and connect to localhost\SQLEXPRESS (Windows Authentication).
Create a new database named liwa_attendance.
Open a New Query and execute the provided SQL scripts to create the four base tables:
Att_Student
Att_Course_Class
Att_Class_List
Att_Class_Attendance
Use the SSMS Import Data task to load the Master Roster into these tables.
B. PostgreSQL & pgvector Compilation (Face DNA)
Since pgvector is written in C++, it must be compiled for Windows:
Download the pgvector source code from their official GitHub and extract it (e.g., C:\pgvector-master).
Open the "x64 Native Tools Command Prompt for VS 2022" as Administrator.
Run the following commands (replace 17 with your PostgreSQL version):
code
Cmd
cd C:\pgvector-master
set "PGROOT=C:\Program Files\PostgreSQL\17"
nmake /f Makefile.win
nmake /f Makefile.win install
Open pgAdmin 4, connect to your server, and create a new database named liwa_attendance.
Open the Query Tool for liwa_attendance and execute:
code
SQL
CREATE EXTENSION vector;

CREATE TABLE "Att_FaceEmbeddings" (
    "iSerial" SERIAL PRIMARY KEY,
    "StudentID" VARCHAR(50),
    "Embedding" vector(512),
    "QualityScore" FLOAT,
    "Symmetry" FLOAT,
    "SourceClassNbr" VARCHAR(50)
);
💻 PHASE 3: Codebase & Environment Setup
1. Backend Setup (Python)
Open a terminal in the root folder of the project.
Install all Python dependencies from the requirements file:
code
Bash
pip install -r requirements.txt
Open main.py and verify the PG_DB_CONFIG settings. Update the password field to match the password you set during the PostgreSQL installation.
2. Frontend Build (React)
The FastAPI server serves the React app as static files, so it must be built locally on the TV first.
Navigate to the frontend directory:
code
Bash
cd frontend
Install Node packages and build the project:
code
Bash
npm install
npm run build
(This generates a /dist folder which main.py routes to the localhost:8000 root).
📧 PHASE 4: Authentication Configuration (Gmail API)
The QR code login system relies on SMTP to send verification links. A standard Gmail password will be blocked by Google security.
Go to the Google Account settings for the sending email address.
Navigate to Security -> 2-Step Verification -> App passwords.
Generate a new 16-character App Password.
Insert this sender email and 16-character password into email_service.py.
🚀 PHASE 5: Running the System
Once all steps are complete, running the application requires only one command.
Open a terminal in the root folder and start the FastAPI server:
code
Bash
python main.py
(Note: On the very first execution, Python will pause to download the buffalo_s InsightFace ONNX models to the hidden ~/.insightface/ directory in the user profile).
Open Microsoft Edge on the Smart TV in full-screen mode and navigate to:
code
Text
http://localhost:8000
The Teacher Authentication QR Code will appear. Scan it with a mobile device, input the registered faculty email, and confirm the login to begin the live tracking session.