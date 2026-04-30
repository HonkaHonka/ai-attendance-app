from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pandas as pd
import uvicorn
import os
import base64
import numpy as np
import pickle
import torch
from PIL import Image
import io
from ultralytics import YOLO
from facenet_pytorch import MTCNN, InceptionResnetV1
from typing import Dict

# =========================================================
# APP SETUP
# =========================================================
app = FastAPI(title="AI Live Attendance Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

device = torch.device("cpu")
print(f"✅ RUNNING ON {device}")

print("🔹 Loading YOLOv8n (PERSON TRACKING)...")
yolo_person = YOLO("yolov8n_openvino_model/", task="detect") 

print("🔹 Loading Face Quality Gate (MTCNN)...")
mtcnn = MTCNN(keep_all=False, device=device)

print("🔹 Loading Face Embedding Model...")
face_net = InceptionResnetV1(pretrained="vggface2").eval().to(device)

DATA_FILE = "data/KHC_REGISTERED_STUDENTS_31560.xlsx"
MEMORY_FILE = "data/face_memory.pkl"
os.makedirs("data", exist_ok=True)

def load_face_db():
    if not os.path.exists(MEMORY_FILE): return {}
    with open(MEMORY_FILE, "rb") as f: return pickle.load(f)

def save_face_db(db):
    with open(MEMORY_FILE, "wb") as f: pickle.dump(db, f)

global_face_db = load_face_db()

def cosine_similarity(a, b):
    a, b = np.array(a).flatten(), np.array(b).flatten()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

try:
    df = pd.read_excel(DATA_FILE)
    df.columns = df.columns.str.strip()
    df = df.fillna("")
except: df = pd.DataFrame()

# =========================================================
# BASIC API ENDPOINTS
# =========================================================
@app.get("/api/login")
def login(email: str):
    user = df[df["Faculty Email"].str.lower() == email.lower()]
    if user.empty: raise HTTPException(404, "Faculty not found")
    return {"status": "success", "name": user.iloc[0]["Faculty Name"]}

@app.get("/api/classes")
def classes(email: str):
    faculty = df[df["Faculty Email"].str.lower() == email.lower()]
    return faculty.drop_duplicates(subset=["Class Nbr"]).to_dict("records")

@app.get("/api/students")
def students(email: str, class_nbr: int):
    class_df = df[(df["Faculty Email"].str.lower() == email.lower()) & (df["Class Nbr"] == class_nbr)]
    return class_df[["Student ID", "Student Name"]].to_dict("records")

# =========================================================
# ENROLLMENT & VERIFICATION
# =========================================================
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
                    with torch.no_grad():
                        emb = face_net(face_tensor.unsqueeze(0).to(device)).cpu().numpy()[0]
                    student_embeddings.append(emb.tolist())
            except Exception: pass
            
    if len(student_embeddings) == 0: raise HTTPException(status_code=400, detail="Could not detect a clear face.")
    global_face_db[payload.student_id] = {"name": payload.student_name, "embeddings": student_embeddings}
    save_face_db(global_face_db) 
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
        with torch.no_grad():
            live_embedding = face_net(face_tensor.unsqueeze(0).to(device)).cpu().numpy()[0]
    except Exception: raise HTTPException(status_code=400, detail="No face detected in the camera.")

    best_match_name = "Unknown"
    best_match_score = -1.0 
    MATCH_THRESHOLD = 0.65 

    for student_id, data in global_face_db.items():
        for saved_embedding in data["embeddings"]:
            sim = cosine_similarity(live_embedding, saved_embedding)
            if sim > best_match_score:
                best_match_score = sim
                best_match_name = data["name"]

    if best_match_score > MATCH_THRESHOLD:
        return {"status": "success", "match": True, "name": best_match_name, "confidence": f"{best_match_score*100:.1f}%"}
    else: return {"status": "success", "match": False, "name": "Unknown"}

# =========================================================
# LIVE SURVEILLANCE CORE
# =========================================================
live_tracker_memory = {} 
next_track_id = 1
RECOGNITION_THRESHOLD = 0.65

def extract_embedding(face_tensor):
    with torch.no_grad():
        return face_net(face_tensor.unsqueeze(0).to(device)).cpu().numpy()[0]

def recognize_face(embedding):
    best_score = -1
    best_id, best_name = None, "Unknown"
    for sid, data in global_face_db.items():
        for saved in data["embeddings"]:
            sim = cosine_similarity(embedding, saved)
            if sim > best_score:
                best_score, best_id, best_name = sim, sid, data["name"]
    if best_score >= RECOGNITION_THRESHOLD: return best_id, best_name, best_score
    return None, "Unknown", best_score

def process_frame(image_b64):
    global live_tracker_memory, next_track_id
    if "," in image_b64: image_b64 = image_b64.split(",")[1]

    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    frame_bgr = np.array(img)[:, :, ::-1]

    results = yolo_person.track(frame_bgr, conf=0.45, iou=0.40, classes=[0], tracker="botsort.yaml", persist=True, verbose=False)
    
    faces_out =[]
    current_frame_tracks = {}
    save_required = False

    if results and results[0].boxes is not None and results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        track_ids = results[0].boxes.id.cpu().numpy()

        for box, track_id in zip(boxes, track_ids):
            if np.isnan(box).any(): continue
            x1, y1, x2, y2 = map(int, box)
            track_id = int(track_id)
            if (x2 - x1) < 40 or (y2 - y1) < 40: continue

            person = live_tracker_memory.get(track_id, {
                "student_id": None, "name": "Scanning...", "status": "scanning", "frames_no_face": 0
            })

            head_h = int((y2 - y1) * 0.40)
            hx1, hy1 = max(0, x1 - 20), max(0, y1 - 20)
            hx2, hy2 = min(img.width, x2 + 20), min(img.height, y1 + head_h + 20)
            
            # 🚀 SAFETY FIX: Prevent crash if crop dimensions are somehow zero
            if hx2 <= hx1 or hy2 <= hy1:
                continue
            
            face_tensor = mtcnn(img.crop((hx1, hy1, hx2, hy2)))
            
            if face_tensor is not None:
                person["frames_no_face"] = 0 
                emb = extract_embedding(face_tensor)
                sid, name, score = recognize_face(emb)

                if sid:
                    person["student_id"], person["name"], person["status"] = sid, name, "known"
                    if 0.80 < score < 0.96 and len(global_face_db[sid]["embeddings"]) < 15:
                        global_face_db[sid]["embeddings"].append(emb.tolist())
                        save_required = True
                else:
                    person["student_id"], person["name"], person["status"] = None, "Unknown", "unknown"
            else:
                person["frames_no_face"] += 1
                if person["frames_no_face"] > 3:
                    person["student_id"], person["name"], person["status"] = None, "No Face", "no_face"

            current_frame_tracks[track_id] = person

            if person["status"] != "no_face":
                faces_out.append({
                    "box":[x1, y1, x2 - x1, y2 - y1],
                    "student_id": person["student_id"],
                    "name": person["name"],
                    "status": person["status"]
                })

    live_tracker_memory = current_frame_tracks
    if save_required: save_face_db(global_face_db)
    return faces_out

@app.websocket("/ws/surveillance")
async def ws_surveillance(ws: WebSocket):
    await ws.accept()
    global live_tracker_memory
    live_tracker_memory.clear()
    try:
        while True:
            data = await ws.receive_json()
            # 🚀 THE FATAL F.conv2d FIX: Removed asyncio.to_thread!
            # Processing sequentially completely eliminates PyTorch CPU memory corruption!
            faces = process_frame(data["image"])
            await ws.send_json({"status": "success", "faces": faces})
    except WebSocketDisconnect:
        live_tracker_memory.clear()

# =========================================================
# QUICK ASSIGN (TEACHER CLICK)
# =========================================================
class AssignPayload(BaseModel):
    student_id: str
    student_name: str
    image: str
    box: list

@app.post("/api/assign-face")
def assign_face(p: AssignPayload):
    if "," in p.image: p.image = p.image.split(",")[1]
    img = Image.open(io.BytesIO(base64.b64decode(p.image))).convert("RGB")
    x, y, w, h = map(int, p.box)
    
    head_h = int(h * 0.40)
    hx1, hy1 = max(0, x - 20), max(0, y - 20)
    hx2, hy2 = min(img.width, x + w + 20), min(img.height, y + head_h + 20)
    
    # Safety check
    if hx2 <= hx1 or hy2 <= hy1:
        raise HTTPException(status_code=400, detail="Invalid crop area.")

    face_tensor = mtcnn(img.crop((hx1, hy1, hx2, hy2)))
    if face_tensor is None:
        raise HTTPException(status_code=400, detail="No face detected. Ensure student is looking at camera.")

    emb = extract_embedding(face_tensor)
    if p.student_id not in global_face_db: global_face_db[p.student_id] = {"name": p.student_name, "embeddings":[]}
    global_face_db[p.student_id]["embeddings"].append(emb.tolist())
    save_face_db(global_face_db)

    global live_tracker_memory
    live_tracker_memory.clear()
    return {"status": "success", "message": "Face Enrolled Successfully!"}

# =========================================================
# EXCEL EXPORT (RESTORED)
# =========================================================
class AttendanceExportPayload(BaseModel):
    class_nbr: int
    attendance_records: Dict[str, str]

@app.post("/api/export-attendance")
def export_attendance(payload: AttendanceExportPayload):
    class_data = df[df['Class Nbr'] == payload.class_nbr].copy()
    if class_data.empty: raise HTTPException(status_code=404, detail="Class not found.")
    
    def get_status(student_id):
        return "Present" if payload.attendance_records.get(str(student_id), "absent") == "present" else "Absent"
        
    class_data['Attendance Status'] = class_data['Student ID'].apply(get_status)
    report_columns =['Student ID', 'Student Name', 'Course Name', 'Start Time', 'Attendance Status']
    report_df = class_data[[c for c in report_columns if c in class_data.columns]]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        report_df.to_excel(writer, index=False, sheet_name='Attendance Report')
    output.seek(0)
    
    headers = { 'Content-Disposition': f'attachment; filename="Attendance_Class_{payload.class_nbr}.xlsx"' }
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

if os.path.exists("frontend/dist"): app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

if __name__ == "__main__": uvicorn.run("main:app", host="0.0.0.0", port=8000)