from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import uvicorn
import os
import base64
import numpy as np
import pickle
import torch
from facenet_pytorch import InceptionResnetV1
from torchvision import transforms # NEW: Replaces MTCNN's resizing job
from PIL import Image
import io
import cv2
from ultralytics import YOLO 
from fastapi.staticfiles import StaticFiles
import asyncio 
from fastapi.responses import StreamingResponse
from typing import Dict

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
device = torch.device('cpu') 
print(f"🚀 AI MODELS WILL RUN ON: {device.type.upper()}")
print("="*50 + "\n")

# 🔥 MTCNN IS GONE! We replace it with lightning-fast standard PyTorch transforms
face_preprocess = transforms.Compose([
    transforms.Resize((160, 160)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])
])

print("Loading InceptionResnetV1 (CPU)...")
resnet = InceptionResnetV1(pretrained='vggface2').eval().to(device) 

# 🔥 LOAD THE NEW YOLO-FACE MODEL 
print("Loading YOLOv8-Face...")
yolo_model = YOLO('yolov8n-face_openvino_model/') # Change to openvino folder later for the TV!
print("✅ ALL CPU AI MODELS ARE READY!")

DATA_FILE = "data/KHC_REGISTERED_STUDENTS_31560.xlsx"
MEMORY_FILE = "data/face_memory.pkl"

if not os.path.exists("data"): os.makedirs("data")

def load_memory():
    if not os.path.exists(MEMORY_FILE): return {}
    try:
        with open(MEMORY_FILE, 'rb') as f: return pickle.load(f)
    except (FileNotFoundError, EOFError): return {}

global_face_db = load_memory()

def save_memory():
    with open(MEMORY_FILE, 'wb') as f: pickle.dump(global_face_db, f)

def get_cosine_similarity(vec1, vec2):
    v1 = np.array(vec1).flatten()
    v2 = np.array(vec2).flatten()
    return np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))

try:
    df = pd.read_excel(DATA_FILE)
    df.columns = df.columns.str.strip() 
    df = df.fillna("")
except Exception as e: df = pd.DataFrame()

# --- BASIC API ENDPOINTS (No changes here) ---
@app.get("/api/login")
def login(email: str):
    user_exists = df[df['Faculty Email'].astype(str).str.lower() == email.lower()]
    if user_exists.empty: raise HTTPException(status_code=404, detail="Faculty email not found.")
    return {"status": "success", "email": email, "name": user_exists.iloc[0]['Faculty Name']}

@app.get("/api/classes")
def get_classes(email: str):
    faculty_data = df[df['Faculty Email'].astype(str).str.lower() == email.lower()]
    cols =['Class Nbr', 'Cass ID', 'Semester', 'Course Code', 'Course Name', 'Start Time', 'End Time', 'Campus Name', 'Room ID']
    return faculty_data.drop_duplicates(subset=['Class Nbr'])[[c for c in cols if c in faculty_data.columns]].to_dict(orient="records")

@app.get("/api/students")
def get_students(email: str, class_nbr: int):
    class_data = df[(df['Faculty Email'].astype(str).str.lower() == email.lower()) & (df['Class Nbr'] == class_nbr)]
    return class_data[['Student ID', 'Student Name']].to_dict(orient="records")


# --- ENROLL ENDPOINT UPDATED (No MTCNN) ---
class EnrollPayload(BaseModel):
    student_id: str
    student_name: str
    class_nbr: str
    images: dict  

@app.post("/api/enroll-face")
def enroll_face(payload: EnrollPayload):
    student_embeddings =[]
    for angle, b64_list in payload.images.items():
        for b64_str in b64_list:
            if not b64_str: continue
            try:
                if ',' in b64_str: b64_str = b64_str.split(',')[1]
                img = Image.open(io.BytesIO(base64.b64decode(b64_str))).convert('RGB')
                
                # Use YOLO-Face to find the bounding box exactly
                res = yolo_model(img, verbose=False)
                if len(res[0].boxes) > 0:
                    x1, y1, x2, y2 = map(int, res[0].boxes.xyxy[0].tolist())
                    face_crop = img.crop((x1, y1, x2, y2))
                    
                    face_tensor = face_preprocess(face_crop).unsqueeze(0).to(device)
                    embedding = resnet(face_tensor).detach().cpu().numpy()
                    student_embeddings.append(embedding[0].tolist())
            except Exception: pass
            
    if len(student_embeddings) == 0: raise HTTPException(status_code=400, detail="Could not detect a clear face.")
    global_face_db[payload.student_id] = {"name": payload.student_name, "embeddings": student_embeddings }
    save_memory() 
    return {"status": "success", "message": f"Successfully memorized {payload.student_name}"}


# 🔥 TRACKER MEMORY (Remembers Who is Who in the Live Stream)
live_tracker_memory = {} # { track_id: {"name": "John", "student_id": "123", "status": "known"} }

def process_surveillance_frame(image_data_str):
    global live_tracker_memory
    save_required = False
    
    if ',' in image_data_str: image_data_str = image_data_str.split(',')[1]
    img_pil = Image.open(io.BytesIO(base64.b64decode(image_data_str))).convert('RGB')
    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    
    # 🔥 FIX 1: Add classes=[0] to strictly ignore chairs, desks, walls, etc.
    results = yolo_model.track(
        img_cv, 
        persist=True, 
        tracker="botsort.yaml", 
        conf=0.6, 
        iou=0.4, 
        classes=[0], # 👈 CRITICAL ADDITION
        verbose=False
    )
    
    MATCH_THRESHOLD = 0.65 
    detected_students =[]

    MAX_SCANS_PER_FRAME = 3
    scans_this_frame = 0

    if results[0].boxes is not None and results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        track_ids = results[0].boxes.id.cpu().numpy()
        confs = results[0].boxes.conf.cpu().numpy() # 🔥 Extract confidences manually

        # 🔥 FIX 2: Zip confidences into the loop
        for box, track_id, conf in zip(boxes, track_ids, confs):
            
            # 🔥 FIX 3: Manually enforce confidence (Tracker sometimes bypasses the conf=0.6 flag)
            if conf < 0.6:
                continue
                
            track_id = int(track_id)
            x1, y1, x2, y2 = map(int, box)
            
            # 🔥 FIX 4: Size Sanity Check. If a box is ridiculously large (e.g., > 60% of the screen), ignore it!
            box_width = x2 - x1
            box_height = y2 - y1
            if box_width > (img_pil.width * 0.6) or box_height > (img_pil.height * 0.6):
                continue # Skip this box, it's a glitch tracking the wall/room
            
            # Pad the bounding box slightly to get the full head
            pad = 10
            x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
            x2, y2 = min(img_pil.width, x2 + pad), min(img_pil.height, y2 + pad)

            if track_id in live_tracker_memory and live_tracker_memory[track_id]["status"] != "scanning":
                person_data = live_tracker_memory[track_id]
            else:
                if scans_this_frame < MAX_SCANS_PER_FRAME:
                    scans_this_frame += 1
                    
                    person_crop = img_pil.crop((x1, y1, x2, y2))
                    face_tensor = face_preprocess(person_crop).unsqueeze(0).to(device)
                    live_embedding = resnet(face_tensor).detach().cpu().numpy()[0]
                    
                    best_match_name = "Unknown"
                    best_match_id = None
                    best_match_score = -1.0 
                    
                    for student_id, data in global_face_db.items():
                        for saved_embedding in data["embeddings"]:
                            similarity = get_cosine_similarity(live_embedding, saved_embedding)
                            if similarity > best_match_score:
                                best_match_score = similarity
                                best_match_name = data["name"]
                                best_match_id = student_id
                                
                    if best_match_score > MATCH_THRESHOLD:
                        confidence_val = best_match_score * 100
                        if 80.0 < confidence_val < 96.0 and len(global_face_db[best_match_id]["embeddings"]) < 20:
                            global_face_db[best_match_id]["embeddings"].append(live_embedding.tolist())
                            save_required = True
                            
                        person_data = {"name": best_match_name, "student_id": best_match_id, "status": "known"}
                    else:
                        person_data = {"name": "Unknown", "student_id": None, "status": "unknown"}
                        
                    live_tracker_memory[track_id] = person_data
                else:
                    person_data = {"name": "Scanning...", "student_id": None, "status": "scanning"}
                    live_tracker_memory[track_id] = person_data

            detected_students.append({
                "name": person_data["name"], 
                "student_id": person_data["student_id"], 
                "box":[x1, y1, box_width, box_height], 
                "status": person_data["status"]
            })

    if save_required: save_memory()
    return detected_students


@app.websocket("/ws/surveillance")
async def websocket_surveillance(websocket: WebSocket):
    await websocket.accept()
    global live_tracker_memory
    live_tracker_memory.clear() # Clear tracker memory when starting fresh
    try:
        while True:
            data = await websocket.receive_json()
            image_b64 = data.get("image")
            detected_faces = await asyncio.to_thread(process_surveillance_frame, image_b64)
            await websocket.send_json({"status": "success", "faces": detected_faces})
    except WebSocketDisconnect:
        print("Live Surveillance Disconnected")


class QuickEnrollPayload(BaseModel):
    student_id: str
    student_name: str
    image: str
    box: list 

@app.post("/api/quick-enroll")
def quick_enroll(payload: QuickEnrollPayload):
    b64_str = payload.image
    if ',' in b64_str: b64_str = b64_str.split(',')[1]
    img_pil = Image.open(io.BytesIO(base64.b64decode(b64_str))).convert('RGB')
    
    x, y, w, h = payload.box
    crop_img = img_pil.crop((max(0, x), max(0, y), min(img_pil.width, x+w), min(img_pil.height, y+h)))
    
    try:
        # Replaced MTCNN with direct Preprocess
        face_tensor = face_preprocess(crop_img).unsqueeze(0).to(device)
        embedding = resnet(face_tensor).detach().cpu().numpy()[0].tolist()
        
        if payload.student_id not in global_face_db:
            global_face_db[payload.student_id] = {"name": payload.student_name, "embeddings":[]}
            
        global_face_db[payload.student_id]["embeddings"].append(embedding)
        save_memory() 
        
        # Clear tracker memory so the Live Stream instantly rescans this face and turns it green!
        global live_tracker_memory
        live_tracker_memory.clear()

        return {"status": "success", "message": f"{payload.student_name} successfully enrolled via Live Click!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail="Could not extract biometric DNA. Please try again.")

# --- 10. NEW: EXPORT ATTENDANCE TO EXCEL ---
class AttendanceExportPayload(BaseModel):
    class_nbr: int
    attendance_records: Dict[str, str]

@app.post("/api/export-attendance")
def export_attendance(payload: AttendanceExportPayload):
    # 1. Get the original list of students for this class from our Excel DB
    class_data = df[df['Class Nbr'] == payload.class_nbr].copy()
    if class_data.empty:
        raise HTTPException(status_code=404, detail="Class not found.")
    
    # 2. Add the live attendance status to the dataframe
    def get_status(student_id):
        # Check the dictionary sent from React. If not present, default to "Absent"
        status = payload.attendance_records.get(str(student_id), "absent")
        return "Present" if status == "present" else "Absent"
        
    class_data['Attendance Status'] = class_data['Student ID'].apply(get_status)
    
    # Optional: Keep only the columns the teacher actually cares about for the report
    report_columns =['Student ID', 'Student Name', 'Course Name', 'Start Time', 'Attendance Status']
    available_cols =[c for c in report_columns if c in class_data.columns]
    report_df = class_data[available_cols]

    # 3. Create the Excel file in memory (RAM) so we don't clutter the hard drive
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        report_df.to_excel(writer, index=False, sheet_name='Attendance Report')
    output.seek(0)
    
    # 4. Stream it back to the browser as a downloadable attachment
    headers = {
        'Content-Disposition': f'attachment; filename="Attendance_Class_{payload.class_nbr}.xlsx"'
    }
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')        

if os.path.exists("frontend/dist"): app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="spa")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)