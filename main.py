from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import uvicorn
import math

app = FastAPI(title="Attendance App Backend")

# Allow our HTML file to talk to this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, this would be your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. Load the Excel Data
DATA_FILE = "data/KHC_REGISTERED_STUDENTS_31560.xlsx"

try:
    df = pd.read_excel(DATA_FILE)
    df.columns = df.columns.str.strip() # Clean up any accidental spaces in column names
    # Convert empty/NaN values to empty strings so they don't break the JSON response
    df = df.fillna("")
except Exception as e:
    print(f"Error loading Excel file: {e}")
    df = pd.DataFrame()

# 2. Login Endpoint
@app.get("/api/login")
def login(email: str):
    """Checks if the email exists in the Excel file."""
    if df.empty:
        raise HTTPException(status_code=500, detail="Database (Excel) not loaded.")
        
    user_exists = df[df['Faculty Email'].astype(str).str.lower() == email.lower()]
    
    if user_exists.empty:
        raise HTTPException(status_code=404, detail="Faculty email not found.")
    
    # Return basic user info
    faculty_name = user_exists.iloc[0]['Faculty Name']
    return {"status": "success", "email": email, "name": faculty_name}

# 3. Get Classes for Faculty
@app.get("/api/classes")
def get_classes(email: str):
    """Returns the unique list of classes for the faculty member."""
    faculty_data = df[df['Faculty Email'].astype(str).str.lower() == email.lower()]
    
    # Select exactly the columns from your screenshot
    columns_to_keep =[
        'Class Nbr', 'Cass ID', 'Semester', 'Course Code', 
        'Course Name', 'Start Time', 'End Time', 'Campus Name', 'Room ID'
    ]
    
    # Filter only columns that actually exist to prevent errors
    available_cols =[col for col in columns_to_keep if col in faculty_data.columns]
    
    # Drop duplicate classes (since rows repeat for every student)
    unique_classes = faculty_data.drop_duplicates(subset=['Class Nbr'])[available_cols]
    
    return unique_classes.to_dict(orient="records")

# 4. Get Students for a Specific Class
@app.get("/api/students")
def get_students(email: str, class_nbr: int):
    """Returns the student list for a specific class."""
    class_data = df[
        (df['Faculty Email'].astype(str).str.lower() == email.lower()) & 
        (df['Class Nbr'] == class_nbr)
    ]
    
    # Select exactly the columns from your screenshot
    student_columns = ['Student ID', 'Student Name']
    available_cols = [col for col in student_columns if col in class_data.columns]
    
    students = class_data[available_cols]
    
    return students.to_dict(orient="records")

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)