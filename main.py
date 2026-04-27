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
import asyncio
from ultralytics import YOLO
from facenet_pytorch import InceptionResnetV1
from torchvision import transforms 

# =========================================================
# APP SETUP
# =========================================================
app = FastAPI(title="AI Live Attendance Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

device = torch.device("cpu")
print(f"✅ RUNNING ON {device}")

# =========================================================
# MODELS
# =========================================================
print("🔹 Loading YOLOv8n-Face (FACE TRACKING)...")
yolo_face = YOLO("yolov8n_openvino_model/", task="detect")

print("🔹 Loading Face Embedding Model...")
face_net = InceptionResnetV1(pretrained="vggface2").eval().to(device)

face_preprocess = transforms.Compose([
    transforms.Resize((160, 160)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])
])

# =========================================================
# DATA & MEMORY
# =========================================================
DATA_FILE = "data/KHC_REGISTERED_STUDENTS_31560.xlsx"
MEMORY_FILE = "data/face_memory.pkl"

os.makedirs("data", exist_ok=True)

def load_face_db():
    if not os.path.exists(MEMORY_FILE):
        return {}
    with open(MEMORY_FILE, "rb") as f:
        return pickle.load(f)

def save_face_db(db):
    with open(MEMORY_FILE, "wb") as f:
        pickle.dump(db, f)

face_db = load_face_db()

def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

# =========================================================
# LOAD EXCEL
# =========================================================
try:
    df = pd.read_excel(DATA_FILE)
    df.columns = df.columns.str.strip()
    df = df.fillna("")
except:
    df = pd.DataFrame()

# =========================================================
# BASIC API
# =========================================================
@app.get("/api/login")
def login(email: str):
    user = df[df["Faculty Email"].astype(str).str.lower() == email.lower()]
    if user.empty:
        raise HTTPException(404, "Faculty not found")
    return {"status": "success", "name": user.iloc[0]["Faculty Name"]}

@app.get("/api/classes")
def classes(email: str):
    faculty = df[df["Faculty Email"].astype(str).str.lower() == email.lower()]
    return faculty.drop_duplicates(subset=["Class Nbr"]).to_dict("records")

@app.get("/api/students")
def students(email: str, class_nbr: int):
    class_df = df[
        (df["Faculty Email"].astype(str).str.lower() == email.lower()) &
        (df["Class Nbr"] == class_nbr)
    ]
    return class_df[["Student ID", "Student Name"]].to_dict("records")

# =========================================================
# RECOGNITION UTILS
# =========================================================
def extract_embedding(face_img: Image.Image):
    t = face_preprocess(face_img).unsqueeze(0).to(device)
    with torch.no_grad():
        return face_net(t).cpu().numpy()[0]

def recognize_face(embedding, threshold=0.65):
    best_score = -1
    best_id = None
    best_name = "Unknown"
    for sid, data in face_db.items():
        for saved in data["embeddings"]:
            sim = cosine_similarity(embedding, saved)
            if sim > best_score:
                best_score = sim
                best_id = sid
                best_name = data["name"]
    if best_score >= threshold:
        return best_id, best_name
    return None, "Unknown"


# =========================================================
# LIVE SURVEILLANCE CORE (FACE-BASED)
# =========================================================
live_tracker_memory = {} 
next_track_id = 1

def process_frame(image_b64):
    global live_tracker_memory, next_track_id
    
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]

    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    frame_rgb = np.array(img)
    frame_bgr = frame_rgb[:, :, ::-1]

    # 🚀 FIX 1: Removed `bytetrack.yaml`. Standard YOLO prediction doesn't crash on NaNs!
    results = yolo_face(frame_bgr, conf=0.45, verbose=False)

    faces_out =[]
    MAX_SCANS_PER_FRAME = 3
    scans_this_frame = 0
    current_frame_tracks = {}

    if results and results[0].boxes is not None and len(results[0].boxes) > 0:
        boxes = results[0].boxes.xyxy.cpu().numpy()

        for box in boxes:
            # 🚀 FIX 2: NaN Safety Net! If OpenVINO spits out garbage math, skip it safely.
            if np.isnan(box).any():
                continue

            x1, y1, x2, y2 = map(int, box)
            box_width = x2 - x1
            box_height = y2 - y1

            # Ignore tiny box artifacts
            if box_width < 25 or box_height < 25:
                continue

            cx = x1 + (box_width // 2)
            cy = y1 + (box_height // 2)

            # --- OUR CUSTOM SPATIAL TRACKER ---
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
                    px2, py2 = min(img.width, x2 + pad_x), min(img.height, y2 + pad_y)
                    
                    face_crop = img.crop((px1, py1, px2, py2))
                    
                    try:
                        emb = extract_embedding(face_crop)
                        sid, name = recognize_face(emb)
                        
                        if sid is not None:
                            person_data = {"name": name, "student_id": sid, "status": "known", "center": (cx, cy)}
                        else:
                            person_data = {"name": "Unknown", "student_id": None, "status": "unknown", "center": (cx, cy)}
                    except Exception as e:
                        person_data = {"name": "Unknown", "student_id": None, "status": "unknown", "center": (cx, cy)}
                        
                    current_frame_tracks[best_track_id] = person_data
                else:
                    person_data = {"name": "Scanning...", "student_id": None, "status": "scanning", "center": (cx, cy)}
                    current_frame_tracks[best_track_id] = person_data

            faces_out.append({
                "box":[x1, y1, box_width, box_height],
                "student_id": person_data["student_id"],
                "name": person_data["name"],
                "status": person_data["status"]
            })

    live_tracker_memory = current_frame_tracks
    return faces_out


# =========================================================
# WEBSOCKET
# =========================================================
@app.websocket("/ws/surveillance")
async def ws_surveillance(ws: WebSocket):
    await ws.accept()
    global live_tracker_memory
    live_tracker_memory.clear()

    try:
        while True:
            data = await ws.receive_json()
            image_b64 = data["image"]
            # Runs safely on a separate thread
            faces = await asyncio.to_thread(process_frame, image_b64)
            await ws.send_json({"status": "success", "faces": faces})
    except WebSocketDisconnect:
        live_tracker_memory.clear()


# =========================================================
# QUICK ASSIGN & 1-ON-1 ENDPOINTS
# =========================================================
class AssignPayload(BaseModel):
    student_id: str
    student_name: str
    image: str
    box: list

@app.post("/api/assign-face")
def assign_face(p: AssignPayload):
    if "," in p.image:
        p.image = p.image.split(",")[1]

    img = Image.open(io.BytesIO(base64.b64decode(p.image))).convert("RGB")
    x, y, w, h = p.box
    pad_x, pad_y = int(w * 0.1), int(h * 0.1)
    face = img.crop((max(0, x-pad_x), max(0, y-pad_y), min(img.width, x+w+pad_x), min(img.height, y+h+pad_y)))

    emb = extract_embedding(face)

    if p.student_id not in face_db:
        face_db[p.student_id] = {"name": p.student_name, "embeddings":[]}

    face_db[p.student_id]["embeddings"].append(emb.tolist())
    save_face_db(face_db)

    global live_tracker_memory
    live_tracker_memory.clear()
    return {"status": "success", "message": "Successfully enrolled!"}

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
                res = yolo_face(np.array(img)[:,:,::-1], verbose=False)
                if len(res[0].boxes) > 0:
                    x1, y1, x2, y2 = map(int, res[0].boxes.xyxy[0].tolist())
                    face_crop = img.crop((x1, y1, x2, y2))
                    embedding = extract_embedding(face_crop)
                    student_embeddings.append(embedding.tolist())
            except Exception: pass
            
    if len(student_embeddings) == 0: raise HTTPException(status_code=400, detail="Could not detect a clear face.")
    face_db[payload.student_id] = {"name": payload.student_name, "embeddings": student_embeddings }
    save_face_db(face_db)
    return {"status": "success", "message": f"Successfully memorized {payload.student_name}"}

class VerifyPayload(BaseModel):
    image: str  

@app.post("/api/verify-face")
def verify_face(payload: VerifyPayload):
    if not face_db: raise HTTPException(status_code=400, detail="Database is empty.")
    try:
        b64_str = payload.image
        if ',' in b64_str: b64_str = b64_str.split(',')[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64_str))).convert('RGB')
        res = yolo_face(np.array(img)[:,:,::-1], verbose=False)
        if len(res[0].boxes) == 0: raise ValueError("No face detected")
        x1, y1, x2, y2 = map(int, res[0].boxes.xyxy[0].tolist())
        live_embedding = extract_embedding(img.crop((x1, y1, x2, y2)))
    except Exception: raise HTTPException(status_code=400, detail="No face detected in the camera.")

    sid, name = recognize_face(live_embedding)
    if sid:
        return {"status": "success", "match": True, "name": name, "confidence": "High"}
    return {"status": "success", "match": False, "name": "Unknown"}

class AttendanceExportPayload(BaseModel):
    class_nbr: int
    attendance_records: dict[str, str]

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

# =========================================================
# FRONTEND
# =========================================================
if os.path.exists("frontend/dist"):
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)