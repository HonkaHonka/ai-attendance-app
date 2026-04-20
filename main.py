from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import uvicorn
import os
import base64
import numpy as np
import pickle
import torch
from facenet_pytorch import MTCNN, InceptionResnetV1
from PIL import Image
import io
import cv2
from ultralytics import YOLO 
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
app = FastAPI(title="Attendance App Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

print("\n" + "="*50)
print(f"🚀 CPU SIMULATION MODE (INTEL OPENVINO TEST)")
# FORCE THE AI ONTO THE CPU TO SIMULATE THE TV
device = torch.device('cpu') 
print(f"🚀 AI MODELS WILL RUN ON: {device.type.upper()}")
print("="*50 + "\n")

print("Loading MTCNN Face Detector (CPU)...")
mtcnn = MTCNN(keep_all=False, device=device) 
print("Loading InceptionResnetV1 (CPU)...")
resnet = InceptionResnetV1(pretrained='vggface2').eval().to(device) 

print("Loading YOLOv8 (Intel OpenVINO Optimized)...")
# WE POINT YOLO TO THE NEW OPENVINO FOLDER INSTEAD OF THE .PT FILE
yolo_model = YOLO('yolov8n_openvino_model/') 

print("✅ ALL CPU AI MODELS ARE READY!")

# 1. Load the Excel Data & AI Memory
DATA_FILE = "data/KHC_REGISTERED_STUDENTS_31560.xlsx"
MEMORY_FILE = "data/face_memory.pkl"

if not os.path.exists("data"):
    os.makedirs("data")

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, 'rb') as f:
            return pickle.load(f)
    except (FileNotFoundError, EOFError):
        return {}

try:
    df = pd.read_excel(DATA_FILE)
    df.columns = df.columns.str.strip() 
    df = df.fillna("")
except Exception as e:
    print(f"Error loading Excel file: {e}")
    df = pd.DataFrame()

@app.get("/api/login")
def login(email: str):
    if df.empty: raise HTTPException(status_code=500, detail="Database (Excel) not loaded.")
    user_exists = df[df['Faculty Email'].astype(str).str.lower() == email.lower()]
    if user_exists.empty: raise HTTPException(status_code=404, detail="Faculty email not found.")
    return {"status": "success", "email": email, "name": user_exists.iloc[0]['Faculty Name']}

@app.get("/api/classes")
def get_classes(email: str):
    faculty_data = df[df['Faculty Email'].astype(str).str.lower() == email.lower()]
    columns_to_keep =['Class Nbr', 'Cass ID', 'Semester', 'Course Code', 'Course Name', 'Start Time', 'End Time', 'Campus Name', 'Room ID']
    available_cols =[col for col in columns_to_keep if col in faculty_data.columns]
    return faculty_data.drop_duplicates(subset=['Class Nbr'])[available_cols].to_dict(orient="records")

@app.get("/api/students")
def get_students(email: str, class_nbr: int):
    class_data = df[(df['Faculty Email'].astype(str).str.lower() == email.lower()) & (df['Class Nbr'] == class_nbr)]
    student_columns =['Student ID', 'Student Name']
    available_cols =[col for col in student_columns if col in class_data.columns]
    return class_data[available_cols].to_dict(orient="records")

# --- 5. ENROLL FACE ENDPOINT ---
class EnrollPayload(BaseModel):
    student_id: str
    student_name: str
    class_nbr: str
    images: dict  

@app.post("/api/enroll-face")
def enroll_face(payload: EnrollPayload):
    face_db = load_memory()
    student_embeddings =[]
    for angle, b64_list in payload.images.items():
        for idx, b64_str in enumerate(b64_list):
            if not b64_str: continue
            try:
                if ',' in b64_str: b64_str = b64_str.split(',')[1]
                image_data = base64.b64decode(b64_str)
                img = Image.open(io.BytesIO(image_data)).convert('RGB')
                
                face_tensor = mtcnn(img)
                if face_tensor is not None:
                    embedding = resnet(face_tensor.unsqueeze(0).to(device)).detach().cpu().numpy()
                    student_embeddings.append(embedding[0].tolist())
            except Exception as e: pass
            
    if len(student_embeddings) == 0: raise HTTPException(status_code=400, detail="Could not detect a clear face.")
         
    face_db[payload.student_id] = {"name": payload.student_name, "embeddings": student_embeddings }
    with open(MEMORY_FILE, 'wb') as f: pickle.dump(face_db, f)
    return {"status": "success", "message": f"Successfully memorized {payload.student_name}"}

# --- 6. VERIFY (1-on-1) FACE ENDPOINT ---
class VerifyPayload(BaseModel):
    image: str  

@app.post("/api/verify-face")
def verify_face(payload: VerifyPayload):
    face_db = load_memory()
    if not face_db: raise HTTPException(status_code=400, detail="Database is empty.")
    try:
        b64_str = payload.image
        if ',' in b64_str: b64_str = b64_str.split(',')[1]
        image_data = base64.b64decode(b64_str)
        img = Image.open(io.BytesIO(image_data)).convert('RGB')
        
        face_tensor = mtcnn(img)
        if face_tensor is None: raise ValueError("No face detected")
        live_embedding = resnet(face_tensor.unsqueeze(0).to(device)).detach().cpu().numpy()[0]
    except Exception: raise HTTPException(status_code=400, detail="No face detected in the camera.")

    best_match_name = "Unknown"
    best_match_score = float("inf") 
    THRESHOLD = 1.0 
    for student_id, data in face_db.items():
        for saved_embedding in data["embeddings"]:
            distance = np.linalg.norm(np.array(live_embedding) - np.array(saved_embedding))
            if distance < best_match_score:
                best_match_score = distance
                best_match_name = data["name"]

    if best_match_score < THRESHOLD:
        confidence = max(0, min(100, (1 - (best_match_score / 1.5)) * 100))
        return {"status": "success", "match": True, "name": best_match_name, "confidence": f"{confidence:.1f}%"}
    else: return {"status": "success", "match": False, "name": "Unknown"}

# --- 7. NEW: LIVE SURVEILLANCE CROWD SWEEP & CONTINUOUS LEARNING ---
class SweepPayload(BaseModel):
    image: str
    class_nbr: str

@app.post("/api/surveillance-sweep")
def surveillance_sweep(payload: SweepPayload):
    face_db = load_memory()
    detected_students =[]
    save_required = False 

    try:
        b64_str = payload.image
        if ',' in b64_str: b64_str = b64_str.split(',')[1]
        image_data = base64.b64decode(b64_str)
        img_pil = Image.open(io.BytesIO(image_data)).convert('RGB')
        img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        
        # YOLO is now running via Intel OpenVINO!
        results = yolo_model(img_cv, classes=[0], verbose=False)
        THRESHOLD = 1.0

        for box in results[0].boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            person_crop = img_pil.crop((x1, y1, x2, y2))
            face_tensor = mtcnn(person_crop)
            
            if face_tensor is not None:
                live_embedding = resnet(face_tensor.unsqueeze(0).to(device)).detach().cpu().numpy()[0]
                
                best_match_name = "Unknown"
                best_match_id = None
                best_match_score = float("inf")
                
                for student_id, data in face_db.items():
                    for saved_embedding in data["embeddings"]:
                        distance = np.linalg.norm(np.array(live_embedding) - np.array(saved_embedding))
                        if distance < best_match_score:
                            best_match_score = distance
                            best_match_name = data["name"]
                            best_match_id = student_id
                            
                if best_match_score < THRESHOLD:
                    confidence_val = (1 - (best_match_score / 1.5)) * 100
                    
                    # --- NEW SMART ACTIVE LEARNING ---
                    # Only learn the face if confidence is between 80% and 96% AND we have less than 20 vectors
                    if 80.0 < confidence_val < 96.0 and len(face_db[best_match_id]["embeddings"]) < 20:
                        face_db[best_match_id]["embeddings"].append(live_embedding.tolist())
                        save_required = True
                        print(f"🧠 SMART LEARNING: Saved new angle for {best_match_name} (Confidence was {confidence_val:.1f}%)")
                        
                    detected_students.append({"name": best_match_name, "student_id": best_match_id, "box":[x1, y1, x2-x1, y2-y1], "status": "known"})
                else:
                    detected_students.append({"name": "Unknown", "student_id": None, "box":[x1, y1, x2-x1, y2-y1], "status": "unknown"})

        # Save to file if the AI learned a new face angle
        if save_required:
            with open(MEMORY_FILE, 'wb') as f: pickle.dump(face_db, f)

        return {"status": "success", "faces": detected_students}
        
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

# --- 8. NEW: LIVE CLICK "QUICK ENROLL" ENDPOINT ---
class QuickEnrollPayload(BaseModel):
    student_id: str
    student_name: str
    image: str
    box: list 

@app.post("/api/quick-enroll")
def quick_enroll(payload: QuickEnrollPayload):
    face_db = load_memory()
    b64_str = payload.image
    if ',' in b64_str: b64_str = b64_str.split(',')[1]
    image_data = base64.b64decode(b64_str)
    img_pil = Image.open(io.BytesIO(image_data)).convert('RGB')
    
    # Safely crop the exact face that was clicked!
    x, y, w, h = payload.box
    pad = 20
    crop_img = img_pil.crop((max(0, x-pad), max(0, y-pad), min(img_pil.width, x+w+pad), min(img_pil.height, y+h+pad)))
    
    try:
        face_tensor = mtcnn(crop_img)
        if face_tensor is None: raise ValueError("MTCNN failed on crop")
        embedding = resnet(face_tensor.unsqueeze(0).to(device)).detach().cpu().numpy()[0].tolist()
        
        if payload.student_id not in face_db:
            face_db[payload.student_id] = {"name": payload.student_name, "embeddings":[]}
            
        face_db[payload.student_id]["embeddings"].append(embedding)
        
        with open(MEMORY_FILE, 'wb') as f: pickle.dump(face_db, f)
        return {"status": "success", "message": f"{payload.student_name} successfully enrolled via Live Click!"}
        
    except Exception as e:
        raise HTTPException(status_code=400, detail="Could not extract biometric DNA. Please try again.")
# --- 9. SERVE REACT FRONTEND (PRODUCTION) ---
# This must be at the bottom so it doesn't block the /api routes!
if os.path.exists("frontend/dist"):
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="spa")
else:
    print("⚠️ WARNING: 'frontend/dist' folder not found. Please run 'npm run build' in the frontend folder.")
if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)