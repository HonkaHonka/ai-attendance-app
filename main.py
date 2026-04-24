from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict
import pandas as pd
import uvicorn
import os
import base64
import numpy as np
import pickle
import torch
from PIL import Image
import io
import cv2
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
print("🔹 Loading YOLOv8n OpenVINO (PERSON TRACKING)...")
yolo_person = YOLO("yolov8n_openvino_model/", task="detect")

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
track_state = {}

# =========================================================
# LOAD EXCEL
# =========================================================
try:
    df = pd.read_excel(DATA_FILE)
    df.columns = df.columns.str.strip()
    df = df.fillna("")
except Exception as e:
    print("⚠️ Excel load error:", e)
    df = pd.DataFrame()

# =========================================================
# BASIC API ENDPOINTS (RESTORED ✅)
# =========================================================
@app.get("/api/login")
def login(email: str):
    user = df[df["Faculty Email"].astype(str).str.lower() == email.lower()]
    if user.empty:
        raise HTTPException(status_code=404, detail="Faculty email not found")
    return {
        "status": "success",
        "email": email,
        "name": user.iloc[0]["Faculty Name"]
    }

@app.get("/api/classes")
def get_classes(email: str):
    faculty = df[df["Faculty Email"].astype(str).str.lower() == email.lower()]
    return faculty.drop_duplicates(subset=["Class Nbr"]).to_dict("records")

@app.get("/api/students")
def get_students(email: str, class_nbr: int):
    class_df = df[
        (df["Faculty Email"].astype(str).str.lower() == email.lower()) &
        (df["Class Nbr"] == class_nbr)
    ]
    return class_df[["Student ID", "Student Name"]].to_dict("records")

# =========================================================
# FACE RECOGNITION UTILS
# =========================================================
def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

def extract_embedding(face_img: Image.Image):
    face_img = face_img.resize((160, 160))
    arr = np.asarray(face_img).astype(np.float32) / 255.0
    t = torch.tensor(arr).permute(2, 0, 1).unsqueeze(0).to(device)
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
# LIVE SURVEILLANCE CORE (TV‑SAFE)
# =========================================================
def process_frame(image_b64):
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]

    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    frame_rgb = np.array(img)
    frame_bgr = frame_rgb[:, :, ::-1]

    orig_h, orig_w = frame_bgr.shape[:2]

    # -------------------------------------------------
    # 1) Downscale for detection (TV stable)
    # -------------------------------------------------
    DET_W, DET_H = 1280, 720
    det_bgr = cv2.resize(frame_bgr, (DET_W, DET_H))
    sx, sy = orig_w / DET_W, orig_h / DET_H

    results = yolo_person.track(
        det_bgr,
        conf=0.40,
        classes=[0],
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

            conf = float(boxes.conf[i])

            # -------------------------------------------------
            # 2) VERY PERMISSIVE threshold for *tracking*
            # -------------------------------------------------
            if conf < 0.25:
                continue

            dx1, dy1, dx2, dy2 = map(int, boxes.xyxy[i])
            x1, y1 = int(dx1 * sx), int(dy1 * sy)
            x2, y2 = int(dx2 * sx), int(dy2 * sy)

            bw, bh = x2 - x1, y2 - y1

            # -------------------------------------------------
            # 3) Allow small boxes for tracking (side seated)
            # -------------------------------------------------
            MIN_TRACK_W = orig_w * 0.025   # ~2.5% width
            MIN_TRACK_H = orig_h * 0.06    # ~6% height

            if bw < MIN_TRACK_W or bh < MIN_TRACK_H:
                continue

            track_id = int(boxes.id[i])

            # -------------------------------------------------
            # 4) Temporal locking (NO drift)
            # -------------------------------------------------
            if track_id in track_state:
                px1, py1, px2, py2 = track_state[track_id]["box"]
                move = abs(x1 - px1) + abs(y1 - py1)

                # Freeze if unstable or low-confidence update
                if conf < 0.45 or move < 30:
                    x1, y1, x2, y2 = px1, py1, px2, py2

            # -------------------------------------------------
            # 5) Camera-aware face ROI (side-profile friendly)
            # -------------------------------------------------
            bw, bh = x2 - x1, y2 - y1

            face_h = int(bh * 0.32)
            face_w = int(bw * 0.75)

            fx1 = x1 + (bw - face_w) // 2
            fx2 = fx1 + face_w
            fy1 = y1 + int(bh * 0.04)
            fy2 = fy1 + face_h

            # -------------------------------------------------
            # 6) Strict gate for *recognition*, not tracking
            # -------------------------------------------------
            face_pixel_h = fy2 - fy1
            face_pixel_w = fx2 - fx1

            MIN_FACE_H = orig_h * 0.05   # ~5% of frame height
            MIN_FACE_W = orig_w * 0.03   # ~3% of frame width

            # Ensure track always exists
            if track_id not in track_state:
                track_state[track_id] = {
                    "student_id": None,
                    "name": "Unknown",
                    "box": (x1, y1, x2, y2)
                }

            # Only recognize when face is good enough
            if (
                track_state[track_id]["student_id"] is None and
                face_pixel_h >= MIN_FACE_H and
                face_pixel_w >= MIN_FACE_W
            ):
                face_crop = img.crop((fx1, fy1, fx2, fy2))
                emb = extract_embedding(face_crop)
                sid, name = recognize_face(emb)

                track_state[track_id]["student_id"] = sid
                track_state[track_id]["name"] = name

            # Update box state
            track_state[track_id]["box"] = (x1, y1, x2, y2)

            person = track_state[track_id]

            faces_out.append({
                "box": [fx1, fy1, fx2 - fx1, fy2 - fy1],
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
            faces = process_frame(data["image"])
            await ws.send_json({"faces": faces})
    except WebSocketDisconnect:
        track_state.clear()

# =========================================================
# FRONTEND (STATIC)
# =========================================================
if os.path.exists("frontend/dist"):
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
