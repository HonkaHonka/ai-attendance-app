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
# 🚀 FIX 1: We use the FACE model directly with the tracker! No more body math!
print("🔹 Loading YOLOv8n-Face (FACE TRACKING)...")
# Ensure this folder name matches your actual converted OpenVINO face model
yolo_face = YOLO("yolov8n-face_openvino_model/", task="detect") 

print("🔹 Loading Face Embedding Model...")
from facenet_pytorch import InceptionResnetV1
face_net = InceptionResnetV1(pretrained="vggface2").eval().to(device)

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
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

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
    user = df[df["Faculty Email"].str.lower() == email.lower()]
    if user.empty:
        raise HTTPException(404, "Faculty not found")
    return {"status": "success", "name": user.iloc[0]["Faculty Name"]}

@app.get("/api/classes")
def classes(email: str):
    faculty = df[df["Faculty Email"].str.lower() == email.lower()]
    return faculty.drop_duplicates(subset=["Class Nbr"]).to_dict("records")

@app.get("/api/students")
def students(email: str, class_nbr: int):
    class_df = df[
        (df["Faculty Email"].str.lower() == email.lower()) &
        (df["Class Nbr"] == class_nbr)
    ]
    return class_df[["Student ID", "Student Name"]].to_dict("records")

# =========================================================
# LIVE SURVEILLANCE CORE (FACE-BASED)
# =========================================================
track_state = {}  # track_id -> info
RECOGNITION_THRESHOLD = 0.65

def extract_embedding(face_img: Image.Image):
    face_img = face_img.resize((160, 160))
    arr = np.asarray(face_img).astype(np.float32) / 255.0
    tensor = torch.tensor(arr).permute(2, 0, 1).unsqueeze(0).to(device)
    with torch.no_grad():
        return face_net(tensor).cpu().numpy()[0]

def recognize_face(embedding):
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

    if best_score >= RECOGNITION_THRESHOLD:
        return best_id, best_name
    return None, "Unknown"

def process_frame(image_b64):
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]

    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    frame_rgb = np.array(img)
    frame_bgr = frame_rgb[:, :, ::-1]

    # 🚀 FIX 2: Let YOLO track the FACES directly!
    results = yolo_face.track(
        frame_bgr,
        conf=0.4,
        tracker="bytetrack.yaml", 
        persist=True,
        verbose=False
    )

    faces_out = []

    if results and results[0].boxes is not None:
        boxes = results[0].boxes

        for i in range(len(boxes)):
            if boxes.id is None:
                continue

            track_id = int(boxes.id[i])
            x1, y1, x2, y2 = map(int, boxes.xyxy[i])

            # The box is ALREADY the face! Just add a tiny pad for ResNet.
            pad_x = int((x2 - x1) * 0.1)
            pad_y = int((y2 - y1) * 0.1)
            
            fx1 = max(0, x1 - pad_x)
            fy1 = max(0, y1 - pad_y)
            fx2 = min(img.width, x2 + pad_x)
            fy2 = min(img.height, y2 + pad_y)

            # Ignore tiny artifact boxes
            if (fx2 - fx1) < 25 or (fy2 - fy1) < 25:
                continue

            if track_id not in track_state:
                face_crop = img.crop((fx1, fy1, fx2, fy2))
                embedding = extract_embedding(face_crop)
                sid, name = recognize_face(embedding)

                track_state[track_id] = {
                    "student_id": sid,
                    "name": name
                }

            person = track_state[track_id]

            faces_out.append({
                "box": [x1, y1, x2 - x1, y2 - y1], # We send the exact YOLO face box to React
                "student_id": person["student_id"],
                "name": person["name"]
            })

    return faces_out

# =========================================================
# WEBSOCKET
# =========================================================
@app.websocket("/ws/surveillance")
async def ws_surveillance(ws: WebSocket):
    await ws.accept()
    track_state.clear()

    try:
        while True:
            data = await ws.receive_json()
            image_b64 = data["image"]
            faces = process_frame(image_b64)
            await ws.send_json({"faces": faces})
    except WebSocketDisconnect:
        track_state.clear()

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
    if "," in p.image:
        p.image = p.image.split(",")[1]

    img = Image.open(io.BytesIO(base64.b64decode(p.image))).convert("RGB")
    x, y, w, h = p.box
    
    pad_x, pad_y = int(w * 0.1), int(h * 0.1)
    face = img.crop((max(0, x-pad_x), max(0, y-pad_y), min(img.width, x+w+pad_x), min(img.height, y+h+pad_y)))

    emb = extract_embedding(face)

    if p.student_id not in face_db:
        face_db[p.student_id] = {"name": p.student_name, "embeddings": []}

    face_db[p.student_id]["embeddings"].append(emb.tolist())
    save_face_db(face_db)

    track_state.clear()
    return {"status": "success"}

# =========================================================
# FRONTEND
# =========================================================
if os.path.exists("frontend/dist"):
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)