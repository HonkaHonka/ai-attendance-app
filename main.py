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
from facenet_pytorch import MTCNN, InceptionResnetV1
from torchvision import transforms 
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

# Native PyTorch Resizer for YOLO face crops
face_preprocess = transforms.Compose([
    transforms.Resize((160, 160)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])
])

print("Loading MTCNN Face Detector (For 1-on-1 Enrollment)...")
mtcnn = MTCNN(keep_all=False, device=device) 

print("Loading InceptionResnetV1 (CPU)...")
resnet = InceptionResnetV1(pretrained='vggface2').eval().to(device) 

print("Loading YOLOv8-Face (Intel OpenVINO Optimized)...")
yolo_model = YOLO('yolov8n-face_openvino_model/', task='detect')
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

# --- ENROLL ENDPOINT (MTCNN for 1-on-1 full screen) ---
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
                
                face_tensor = mtcnn(img)
                if face_tensor is not None:
                    embedding = resnet(face_tensor.unsqueeze(0).to(device)).detach().cpu().numpy()
                    student_embeddings.append(embedding[0].tolist())
            except Exception: pass
            
    if len(student_embeddings) == 0: raise HTTPException(status_code=400, detail="Could not detect a clear face.")
    global_face_db[payload.student_id] = {"name": payload.student_name, "embeddings": student_embeddings }
    save_memory() 
    return {"status": "success", "message": f"Successfully memorized {payload.student_name}"}


class VerifyPayload(BaseModel):
    image: str  

@app.post("/api/verify-face")
def verify_face(payload: VerifyPayload):
    if not global_face_db: raise HTTPException(status_code=400, detail="Database is empty.")
    try:
        b64_str = payload.image
        if ',' in b64_str: b64_str = b64_str.split(',')[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64_str))).convert('RGB')
        
        face_tensor = mtcnn(img)
        if face_tensor is None: raise ValueError("No face detected")
        live_embedding = resnet(face_tensor.unsqueeze(0).to(device)).detach().cpu().numpy()[0]
    except Exception: raise HTTPException(status_code=400, detail="No face detected in the camera.")

    best_match_name = "Unknown"
    best_match_score = -1.0 
    MATCH_THRESHOLD = 0.65 

    for student_id, data in global_face_db.items():
        for saved_embedding in data["embeddings"]:
            similarity = get_cosine_similarity(live_embedding, saved_embedding)
            if similarity > best_match_score:
                best_match_score = similarity
                best_match_name = data["name"]

    if best_match_score > MATCH_THRESHOLD:
        confidence = best_match_score * 100
        return {"status": "success", "match": True, "name": best_match_name, "confidence": f"{confidence:.1f}%"}
    else: return {"status": "success", "match": False, "name": "Unknown"}


# 🔥 DIRECT FACE TRACKER MEMORY
live_tracker_memory = {} 
next_track_id = 1

def process_surveillance_frame(image_data_str):
    global live_tracker_memory, next_track_id
    save_required = False
    
    if ',' in image_data_str: image_data_str = image_data_str.split(',')[1]
    img_pil = Image.open(io.BytesIO(base64.b64decode(image_data_str))).convert('RGB')
    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    
    # 🚀 FIX 1: INCREASE CONFIDENCE & ADD NMS (iou)
    # conf=0.65 kills the background noise. iou=0.4 stops overlapping boxes. max_det=40 limits the AI from crashing.
    results = yolo_model(img_cv, conf=0.65, iou=0.4, max_det=40, verbose=False)
    
    MATCH_THRESHOLD = 0.65 
    detected_students =[]
    MAX_SCANS_PER_FRAME = 3
    scans_this_frame = 0
    current_frame_tracks = {} 

    if results[0].boxes is not None and len(results[0].boxes) > 0:
        boxes = results[0].boxes.xyxy.cpu().numpy()

        for box in boxes:
            x1, y1, x2, y2 = map(int, box)
            box_width = x2 - x1
            box_height = y2 - y1

            # 🚀 FIX 2: GEOMETRIC SANITY CHECKS
            # 1. Size Check: Ignore anything smaller than 35x35 pixels (kills tiny shadows)
            if box_width < 35 or box_height < 35: 
                continue
            
            # 2. Aspect Ratio: Faces are vertical rectangles. 
            # If width is more than 1.2x the height, it's a cabinet/shirt fold. Kill it.
            aspect_ratio = float(box_width) / float(box_height)
            if aspect_ratio < 0.5 or aspect_ratio > 1.2:
                continue

            cx = x1 + (box_width // 2)
            cy = y1 + (box_height // 2)

            best_track_id = None
            min_dist = float('inf')
            dist_threshold = max(box_width * 1.5, 100)

            for tid, tdata in live_tracker_memory.items():
                prev_cx, prev_cy = tdata["center"]
                dist = ((cx - prev_cx)**2 + (cy - prev_cy)**2) ** 0.5
                if dist < dist_threshold and dist < min_dist:
                    min_dist = dist
                    best_track_id = tid

            if best_track_id is not None and live_tracker_memory[best_track_id]["status"] != "scanning":
                person_data = live_tracker_memory[best_track_id]
                person_data["center"] = (cx, cy) 
                current_frame_tracks[best_track_id] = person_data
            else:
                if best_track_id is None:
                    best_track_id = next_track_id
                    next_track_id += 1

                if scans_this_frame < MAX_SCANS_PER_FRAME:
                    scans_this_frame += 1
                    
                    pad_x = int(box_width * 0.1)
                    pad_y = int(box_height * 0.1)
                    px1, py1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
                    px2, py2 = min(img_pil.width, x2 + pad_x), min(img_pil.height, y2 + pad_y)
                    
                    face_crop = img_pil.crop((px1, py1, px2, py2))
                    
                    try:
                        face_tensor = face_preprocess(face_crop).unsqueeze(0).to(device)
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
                                
                            person_data = {"name": best_match_name, "student_id": best_match_id, "status": "known", "center": (cx, cy)}
                        else:
                            person_data = {"name": "Unknown", "student_id": None, "status": "unknown", "center": (cx, cy)}
                    except Exception as e:
                        person_data = {"name": "Unknown", "student_id": None, "status": "unknown", "center": (cx, cy)}
                        
                    current_frame_tracks[best_track_id] = person_data
                else:
                    person_data = {"name": "Scanning...", "student_id": None, "status": "scanning", "center": (cx, cy)}
                    current_frame_tracks[best_track_id] = person_data

            detected_students.append({
                "name": person_data["name"], "student_id": person_data["student_id"], 
                "box":[x1, y1, box_width, box_height], "status": person_data["status"]
            })

    live_tracker_memory = current_frame_tracks
    if save_required: save_memory()
    return detected_students

@app.websocket("/ws/surveillance")
async def websocket_surveillance(websocket: WebSocket):
    await websocket.accept()
    global live_tracker_memory
    live_tracker_memory.clear() 
    try:
        while True:
            data = await websocket.receive_json()
            image_b64 = data.get("image")
            detected_faces = await asyncio.to_thread(process_surveillance_frame, image_b64)
            await websocket.send_json({"status": "success", "faces": detected_faces})
    except WebSocketDisconnect:
        print("Live Surveillance Disconnected")

class SweepPayload(BaseModel):
    image: str
    class_nbr: str

@app.post("/api/surveillance-sweep")
def surveillance_sweep_endpoint(payload: SweepPayload):
    try:
        detected_faces = process_surveillance_frame(payload.image)
        return {"status": "success", "faces": detected_faces}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
    pad_x, pad_y = int(w * 0.1), int(h * 0.1)
    crop_img = img_pil.crop((max(0, x-pad_x), max(0, y-pad_y), min(img_pil.width, x+w+pad_x), min(img_pil.height, y+h+pad_y)))
    
    try:
        face_tensor = face_preprocess(crop_img).unsqueeze(0).to(device)
        embedding = resnet(face_tensor).detach().cpu().numpy()[0].tolist()
        
        if payload.student_id not in global_face_db:
            global_face_db[payload.student_id] = {"name": payload.student_name, "embeddings":[]}
            
        global_face_db[payload.student_id]["embeddings"].append(embedding)
        save_memory() 
        
        global live_tracker_memory
        live_tracker_memory.clear()

        return {"status": "success", "message": f"{payload.student_name} successfully enrolled via Live Click!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail="Could not extract biometric DNA. Please try again.")

class AttendanceExportPayload(BaseModel):
    class_nbr: int
    attendance_records: Dict[str, str]

@app.post("/api/export-attendance")
def export_attendance(payload: AttendanceExportPayload):
    class_data = df[df['Class Nbr'] == payload.class_nbr].copy()
    if class_data.empty: raise HTTPException(status_code=404, detail="Class not found.")
    
    def get_status(student_id):
        status = payload.attendance_records.get(str(student_id), "absent")
        return "Present" if status == "present" else "Absent"
        
    class_data['Attendance Status'] = class_data['Student ID'].apply(get_status)
    
    report_columns =['Student ID', 'Student Name', 'Course Name', 'Start Time', 'Attendance Status']
    available_cols =[c for c in report_columns if c in class_data.columns]
    report_df = class_data[available_cols]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        report_df.to_excel(writer, index=False, sheet_name='Attendance Report')
    output.seek(0)
    
    headers = { 'Content-Disposition': f'attachment; filename="Attendance_Class_{payload.class_nbr}.xlsx"' }
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')        

if os.path.exists("frontend/dist"): app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="spa")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)