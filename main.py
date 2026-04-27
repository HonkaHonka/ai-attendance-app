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
from facenet_pytorch import MTCNN, InceptionResnetV1

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
print("🔹 Loading YOLOv8n (PERSON TRACKING)...")
yolo_person = YOLO("yolov8n_openvino_model/", task="detect") # Using standard person detection

print("🔹 Loading Face Quality Gate (MTCNN)...")
mtcnn = MTCNN(keep_all=False, device=device)

print("🔹 Loading Face Embedding Model...")
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

try:
    df = pd.read_excel(DATA_FILE)
    df.columns = df.columns.str.strip()
    df = df.fillna("")
except:
    df = pd.DataFrame()

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
# LIVE SURVEILLANCE CORE (PERSON-BASED WITH FACE ROI)
# =========================================================
track_state = {}  # track_id -> info
RECOGNITION_THRESHOLD = 0.65

def extract_embedding(face_tensor):
    with torch.no_grad():
        return face_net(face_tensor.unsqueeze(0).to(device)).cpu().numpy()[0]

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
        return best_id, best_name, best_score
    return None, "Unknown", best_score

def process_frame(image_b64):
    global track_state
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]

    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    frame_rgb = np.array(img)
    frame_bgr = frame_rgb[:, :, ::-1]

    # 🚀 FIX 1: Use BotSORT. It tracks perfectly and avoids the OpenVINO ByteTrack NaN bug.
    # We enforce NMS (iou=0.4) to stop overlapping boxes.
    results = yolo_person.track(
        frame_bgr,
        conf=0.50,
        iou=0.40,
        classes=[0],                 # person only
        tracker="botsort.yaml",      
        persist=True,
        verbose=False
    )

    faces_out =[]
    current_frame_tracks = {}

    if results and results[0].boxes is not None and results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        track_ids = results[0].boxes.id.cpu().numpy()

        for box, track_id in zip(boxes, track_ids):
            if np.isnan(box).any(): continue

            x1, y1, x2, y2 = map(int, box)
            track_id = int(track_id)

            # Prevent massive full-screen boxes or tiny glitches
            if (x2 - x1) < 40 or (y2 - y1) < 40: continue

            # Extract Top 35% of the body (The Head ROI)
            head_h = int((y2 - y1) * 0.35)
            hx1, hy1, hx2, hy2 = x1, y1, x2, y1 + head_h
            
            # Pad it slightly
            pad = 20
            hx1 = max(0, hx1 - pad)
            hy1 = max(0, hy1 - pad)
            hx2 = min(img.width, hx2 + pad)
            hy2 = min(img.height, hy2 + pad)

            if track_id not in track_state:
                # 🚀 QUALITY GATE: Pass the Head ROI to MTCNN
                head_crop = img.crop((hx1, hy1, hx2, hy2))
                face_tensor = mtcnn(head_crop)
                
                if face_tensor is not None:
                    # MTCNN found a real face! Extract DNA.
                    emb = extract_embedding(face_tensor)
                    sid, name, score = recognize_face(emb)

                    # Continuous Learning trigger
                    if sid and 0.80 < score < 0.96 and len(face_db[sid]["embeddings"]) < 20:
                        face_db[sid]["embeddings"].append(emb.tolist())
                        save_face_db(face_db)

                    track_state[track_id] = {"student_id": sid, "name": name, "status": "known" if sid else "unknown"}
                else:
                    # MTCNN rejected it (person looking away, or it was just a shadow)
                    track_state[track_id] = {"student_id": None, "name": "Scanning Face...", "status": "scanning"}
            
            person = track_state[track_id]
            current_frame_tracks[track_id] = person

            faces_out.append({
                "box":[x1, y1, x2 - x1, y2 - y1], # We return the whole body box for UI clicks!
                "student_id": person["student_id"],
                "name": person["name"],
                "status": person["status"]
            })

    # Clear memory of people who left the frame
    track_state = current_frame_tracks
    return faces_out


@app.websocket("/ws/surveillance")
async def ws_surveillance(ws: WebSocket):
    await ws.accept()
    track_state.clear()
    try:
        while True:
            data = await ws.receive_json()
            image_b64 = data["image"]
            faces = await asyncio.to_thread(process_frame, image_b64)
            await ws.send_json({"status": "success", "faces": faces})
    except WebSocketDisconnect:
        track_state.clear()


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
    
    # Extract just the top 35% of the body that was clicked
    head_h = int(h * 0.35)
    pad = 20
    hx1, hy1 = max(0, x - pad), max(0, y - pad)
    hx2, hy2 = min(img.width, x + w + pad), min(img.height, y + head_h + pad)
    
    head_crop = img.crop((hx1, hy1, hx2, hy2))
    face_tensor = mtcnn(head_crop)
    
    if face_tensor is None:
        raise HTTPException(status_code=400, detail="No face detected. Please ensure the student is looking at the camera.")

    emb = extract_embedding(face_tensor)

    if p.student_id not in face_db:
        face_db[p.student_id] = {"name": p.student_name, "embeddings": []}

    face_db[p.student_id]["embeddings"].append(emb.tolist())
    save_face_db(face_db)

    track_state.clear()
    return {"status": "success", "message": "Face Enrolled Successfully!"}

if os.path.exists("frontend/dist"):
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)