import os
os.environ["OPENVINO_DEVICE"] = "GPU"

# 🎯 MONKEY-PATCH: Intercept OpenVINO compile_model and force GPU
import openvino as ov
_original_compile_model = ov.Core.compile_model

def _patched_compile_model(self, model, device_name=None, config=None):
    if device_name in ("AUTO", "AUTO:CPU,GPU", "AUTO:GPU,CPU", None, "CPU"):
        print(f"🔄 OpenVINO device override: {device_name} → GPU")
        device_name = "GPU"
    return _original_compile_model(self, model, device_name, config)

ov.Core.compile_model = _patched_compile_model
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from starlette.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pandas as pd
import uvicorn
import psutil
import base64
import numpy as np
from fastapi.responses import FileResponse
import torch
from PIL import Image
import io
import asyncio
from ultralytics import YOLO
from facenet_pytorch import InceptionResnetV1
import cv2
from typing import Dict
import time
from typing import Optional
from fastapi import UploadFile, File
from fastapi import Request
import psycopg2
from pgvector.psycopg2 import register_vector
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
    expose_headers=["X-QR-Token"]  # <--- ADD THIS LINE
)

device = torch.device("cpu")
print(f"✅ RUNNING ON {device}")

print("🔹 Loading YOLOv8n (PERSON TRACKING)...")
yolo_person = YOLO("yolov8n_openvino_model/", task="detect")

# 🎯 Warmup: forces predictor initialization so first real frame is fast
dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
_ = yolo_person.track(dummy_frame, verbose=False, persist=True)
print("✅ YOLO OpenVINO model loaded")

print("🔹 Loading Face Quality Gate (YuNet)...")
import socket

def get_local_ip():
    """Gets the actual local network IP (e.g. 192.168.0.101) of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't have to be reachable, just forces the OS to pick the right network interface
        s.connect(("8.8.8.8", 80)) 
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

class YuNetMTCNNWrapper:
    def __init__(self, model_path="face_detection_yunet_2023mar.onnx"):
        self.detector = cv2.FaceDetectorYN.create(
            model=model_path,
            config="",
            input_size=(320, 320),
            score_threshold=0.75,
            nms_threshold=0.3,
            top_k=1
        )
    
    def detect(self, img, landmarks=False):
        arr = np.array(img)
        h, w = arr.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(arr)
        
        if faces is None or len(faces) == 0:
            return None, None, None
        
        boxes, probs, lms = [], [], []
        for f in faces:
            boxes.append([f[0], f[1], f[2], f[3]])
            probs.append(float(f[14]))
            lms.append([
                [f[4], f[5]],   # right eye
                [f[6], f[7]],   # left eye
                [f[8], f[9]],   # nose
                [f[10], f[11]], # right mouth
                [f[12], f[13]]  # left mouth
            ])
        
        # Return in MTCNN format: list of np arrays so f_boxes[0] works in your code
        return [np.array(boxes)], [np.array(probs)], [np.array(lms)]
    
    def extract(self, img, boxes, save_path=None):
        if boxes is None or len(boxes) == 0:
            return None
        
        tensors = []
        for box in boxes:
            x, y, w, h = map(float, box)
            margin = 0.2
            x1 = max(0, int(x - w * margin))
            y1 = max(0, int(y - h * margin))
            x2 = min(img.width, int(x + w * (1 + margin)))
            y2 = min(img.height, int(y + h * (1 + margin)))
            
            face = img.crop((x1, y1, x2, y2))
            face = face.resize((160, 160))
            arr = np.array(face).astype(np.float32) / 255.0
            tensor = torch.from_numpy(arr).permute(2, 0, 1)
            tensor = (tensor - 0.5) / 0.5   # normalize to [-1, 1] like MTCNN
            tensors.append(tensor)
        
        if len(tensors) == 1:
            return tensors[0]
        return tensors
    
    def __call__(self, img):
        # Used by enroll_face & verify_face
        boxes, probs, lms = self.detect(img, landmarks=True)
        if boxes is None or boxes[0] is None or len(boxes[0]) == 0:
            return None
        return self.extract(img, boxes[0][:1])

mtcnn = YuNetMTCNNWrapper("face_detection_yunet_2023mar.onnx")
print("✅ YuNet face detector loaded")

print("🔹 Loading Face Embedding Model...")
face_net = InceptionResnetV1(pretrained="vggface2").eval().to(device)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
frontend_dist = os.path.join(BASE_DIR, "frontend", "dist")

# Serve static assets first
app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

# Serve index.html for all other routes (SPA behavior)
@app.get("/")
def serve_index():
    return FileResponse(os.path.join(frontend_dist, "index.html"))

@app.get("/{path:path}")
def serve_spa(path: str):
    # Don't intercept API routes
    if path.startswith("api/") or path.startswith("ws/"):
        raise HTTPException(status_code=404)
    return FileResponse(os.path.join(frontend_dist, "index.html"))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)


global_face_db = {}

def cosine_similarity(a, b):
    a, b = np.array(a).flatten(), np.array(b).flatten()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
# --- POSTGRESQL DATABASE CONFIGURATION ---
DB_CONFIG = {
    "dbname": "liwa_attendance",
    "user": "postgres",
    "password": "aze123",  # <--- CHANGE THIS TO YOUR POSTGRES PASSWORD!
    "host": "localhost",
    "port": "5432"
}

def get_db():
    conn = psycopg2.connect(**DB_CONFIG)
    register_vector(conn) # Tells psycopg2 how to handle the 512d FaceNet arrays
    return conn



import secrets
import qrcode
from io import BytesIO
from fastapi.responses import StreamingResponse, HTMLResponse

# Import email service (add at top of file with other imports)
from email_service import send_verification_email
EMAIL_ENABLED = True

# QR Session store: token → {status, email, name, created_at, verify_token}
qr_sessions = {}

# =========================================
# QR CODE AUTHENTICATION
# =========================================

@app.get("/api/generate-qr")
def generate_qr(frontend_url: str = "http://127.0.0.1:8000"): 
    """TV calls this to get a QR code for teacher authentication."""
    token = secrets.token_urlsafe(32)
    
    qr_sessions[token] = {
        "status": "pending",
        "email": None,
        "faculty_name": None,
        "verify_token": None,
        "created_at": time.time()
    }
    
    _clean_old_sessions()
    
    # 🎯 THE MAGIC TRICK: 
    # If the TV is using localhost, force the QR code to use the real Network IP
    if "localhost" in frontend_url or "127.0.0.1" in frontend_url:
        network_ip = get_local_ip()
        auth_url = f"http://{network_ip}:8000/?token={token}"
    else:
        auth_url = f"{frontend_url}/?token={token}"
        
    print(f"📱 QR Code generated targeting: {auth_url}") # Helpful for debugging!
    
    qr = qrcode.QRCode(version=3, box_size=12, border=2)
    qr.add_data(auth_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="#2f3254", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    
    return StreamingResponse(buf, media_type="image/png", headers={
        "X-QR-Token": token
    })

def _clean_old_sessions():
    """Remove expired QR sessions."""
    current_time = time.time()
    expired = [t for t, data in qr_sessions.items() 
               if current_time - data["created_at"] > 600]  # 10 min expiry
    for t in expired:
        del qr_sessions[t]

@app.get("/api/check-qr-status")
def check_qr_status(token: str):
    """TV polls this every 2 seconds to check if teacher verified."""
    session = qr_sessions.get(token)
    if not session:
        raise HTTPException(404, "Session expired or invalid")
    
    if time.time() - session["created_at"] > 600:
        del qr_sessions[token]
        raise HTTPException(410, "Session expired")
    
    return {
        "status": session["status"],  # "pending", "email_entered", "approved"
        "email": session.get("email"),
        "name": session.get("faculty_name")
    }



@app.post("/api/request-email-verification")
def request_email_verification(payload: dict, request: Request): 
    token = payload.get("token")
    email = payload.get("email")
    
    if not token or not email:
        raise HTTPException(400, "Token and email required")
    
    session = qr_sessions.get(token)
    if not session:
        raise HTTPException(400, "Invalid or expired QR session")
        
    if session["status"] == "email_entered" and session.get("email") == email:
        return {"status": "success", "message": f"Verification email already sent to {email}"}
        
    if session["status"] != "pending":
        raise HTTPException(400, "QR session is no longer valid")
    
    # 🎯 NEW: Check the PostgreSQL Database instead of Pandas/Excel!
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT "FacultyName" FROM "Att_Course_Class" WHERE LOWER("FacultyID") = LOWER(%s) LIMIT 1', (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        raise HTTPException(500, f"Database error: {str(e)}")
    
    if not user:
        raise HTTPException(404, "Faculty email not found in university database")
    
    verify_token = secrets.token_urlsafe(32)
    session["status"] = "email_entered"
    session["email"] = email
    session["faculty_name"] = user[0] # Extracted from the DB query
    session["verify_token"] = verify_token
    
    host_url = str(request.base_url).rstrip("/")
    verify_url = f"{host_url}/api/confirm-verification?vt={verify_token}&t={token}"
    
    if EMAIL_ENABLED:
        success = send_verification_email(
            to_email=email,
            teacher_name=session["faculty_name"],
            verify_url=verify_url
        )
        if not success:
            session["status"] = "pending" 
            raise HTTPException(500, "Failed to send verification email")
    else:
        print(f"\n{'='*60}\n📧 DEV MODE: Verification URL for {email}:\n   {verify_url}\n{'='*60}\n")
    
    return {"status": "success", "message": f"Verification email sent to {email}"}

@app.get("/api/confirm-verification")
def confirm_verification(vt: str, t: str):
    """
    Teacher clicks link in email → this endpoint approves the session.
    Returns HTML page that auto-redirects or shows success.
    """
    session = qr_sessions.get(t)
    if not session or session.get("verify_token") != vt:
        return HTMLResponse("""
        <html><body style="font-family: Arial; text-align: center; padding: 50px;">
            <h1 style="color: #dc3545;">❌ Invalid or Expired Link</h1>
            <p>This verification link has expired or is invalid.</p>
            <a href="/" style="color: #2f3254;">Go back to portal</a>
        </body></html>
        """)
    
    # Approve session
    session["status"] = "approved"
    
    return HTMLResponse(f"""
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 40px 20px; 
                   background: linear-gradient(135deg, #2f3254 0%, #1a1a2e 100%); min-height: 100vh; 
                   margin: 0; display: flex; align-items: center; justify-content: center; }}
            .card {{ background: white; padding: 40px; border-radius: 16px; 
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3); max-width: 400px; width: 100%; }}
            h1 {{ color: #28a745; margin-bottom: 10px; }}
            p {{ color: #666; line-height: 1.6; }}
            .icon {{ font-size: 64px; margin-bottom: 20px; }}
            .name {{ color: #2f3254; font-weight: bold; font-size: 18px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">✅</div>
            <h1>Verification Successful!</h1>
            <p>Welcome, <span class="name">{session['faculty_name']}</span></p>
            <p>You can now return to the classroom screen.<br>It will automatically log you in.</p>
            <p style="margin-top: 30px; color: #888; font-size: 12px;">
                Liwa University Faculty Portal
            </p>
        </div>
    </body>
    </html>
    """)

# =========================================================
# BASIC API ENDPOINTS
# =========================================================
@app.get("/api/login")
def login(email: str):
    conn = get_db()
    cur = conn.cursor()
    # Search for the teacher in the Course table
    cur.execute('SELECT "FacultyName" FROM "Att_Course_Class" WHERE LOWER("FacultyID") = LOWER(%s) LIMIT 1', (email,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    
    if not user: 
        raise HTTPException(404, "Faculty email not found in database")
    return {"status": "success", "name": user[0]}

@app.get("/api/classes")
def classes(email: str, room_id: str = None):
    conn = get_db()
    cur = conn.cursor()
    
    query = 'SELECT "ClassNbr", "sTerm" AS "Semester", "Code" AS "Course Code", "CourseName" AS "Course Name", "StartTime" AS "Start Time", "RoomID" AS "Room ID" FROM "Att_Course_Class" WHERE LOWER("FacultyID") = LOWER(%s)'
    params = [email]
    
    # TV Room ID filtering logic!
    if room_id:
        query += ' AND "RoomID" = %s'
        params.append(room_id)
        
    cur.execute(query, tuple(params))
    
    # Convert DB rows to JSON list for React
    columns = [desc[0] for desc in cur.description]
    results = [dict(zip(columns, row)) for row in cur.fetchall()]
    
    cur.close()
    conn.close()
    return results

@app.get("/api/students")
def students(email: str, class_nbr: str):
    print(f"🔍 Loading students for class_nbr='{class_nbr}', email='{email}'")
    
    conn = get_db()
    cur = conn.cursor()
    
    # Load ALL embeddings for students in THIS class from PostgreSQL only
    cur.execute('''
        SELECT s."StudentID", 
               s."StudentName",
               f."Embedding"
        FROM "Att_Student" s
        JOIN "Att_Class_List" cl ON s."StudentID" = cl."StudentID"
        LEFT JOIN "Att_FaceEmbeddings" f ON s."StudentID" = f."StudentID"
        WHERE cl."ClassNbr" = %s
    ''', (str(class_nbr),))
    
    raw_rows = cur.fetchall()
    print(f"🔍 Found {len(raw_rows)} raw rows from database")
    
    # Group embeddings by student
    from collections import defaultdict
    student_embeddings = defaultdict(list)
    student_names = {}
    
    for row in raw_rows:
        sid, sname, emb = row
        student_names[sid] = sname
        if emb is not None:
            student_embeddings[sid].append(emb)
        print(f"🔍 Row: {sid}, {sname}, has_embedding={emb is not None}")
    
    # Build student list for React
    student_list = []
    for sid, sname in student_names.items():
        student_list.append({
            "Student ID": sid,
            "Student Name": sname
        })
    
    # Build fresh face_db (atomic swap) — ONLY from PostgreSQL
    new_face_db = {}
    for sid, sname in student_names.items():
        embs = student_embeddings.get(sid, [])
        if embs:
            new_face_db[sid] = {
                "name": sname,
                "embeddings": embs
            }
    
    global_face_db.clear()
    global_face_db.update(new_face_db)
    
    total_embs = sum(len(v["embeddings"]) for v in global_face_db.values())
    print(f"🧠 AI Memory: {len(global_face_db)} students, {total_embs} embeddings for class {class_nbr}")
    
    cur.close()
    conn.close()
    return student_list

@app.get("/api/db-health")
def db_health():
    """Query PostgreSQL directly for embedding quality."""
    report = []
    
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Get all students with their embeddings
        cur.execute('''
            SELECT s."StudentID", s."StudentName", 
                   COUNT(f."iSerial") as emb_count,
                   ARRAY_AGG(f."Embedding") as embeddings
            FROM "Att_Student" s
            LEFT JOIN "Att_FaceEmbeddings" f ON s."StudentID" = f."StudentID"
            GROUP BY s."StudentID", s."StudentName"
        ''')
        
        for row in cur.fetchall():
            sid, sname, emb_count, embeddings = row
            embeddings = embeddings or []
            
            if emb_count > 1:
                sims = []
                clean_embs = [e for e in embeddings if e is not None]
                for i in range(len(clean_embs)):
                    for j in range(i+1, len(clean_embs)):
                        sims.append(cosine_similarity(clean_embs[i], clean_embs[j]))
                avg_sim = float(np.mean(sims)) if sims else 1.0
                min_sim = float(np.min(sims)) if sims else 1.0
            else:
                avg_sim, min_sim = 1.0, 1.0
            
            flag = "OK"
            if emb_count > 8:
                flag = "TOO_MANY_EMBS"
            elif emb_count == 0:
                flag = "EMPTY"
            elif min_sim < 0.75:
                flag = "HIGH_VARIANCE"
            
            report.append({
                "student_id": sid,
                "name": sname,
                "embedding_count": emb_count,
                "avg_self_similarity": round(avg_sim, 3),
                "min_self_similarity": round(min_sim, 3),
                "flag": flag
            })
        
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"⚠️ DB health query failed: {e}")
        return {"error": str(e)}
    
    suspicious = [r for r in report if r["flag"] != "OK"]
    return {
        "total_students": len(report),
        "suspicious_count": len(suspicious),
        "suspicious": suspicious,
        "students": report
    }

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "camera": "connected",
        "db_students": len(global_face_db),
        "db_corrupted": False,
        "session_active": session_stats["start_time"] is not None,
        "total_embeddings": sum(len(v["embeddings"]) for v in global_face_db.values())
    }

@app.get("/api/session-report")
def session_report():
    """Report on what happened during the CURRENT 5-minute class."""
    if not session_stats["start_time"]:
        return {"message": "No session started yet."}
    
    duration = time.time() - session_stats["start_time"]
    avg_sym = float(np.mean(session_stats["avg_symmetry"])) if session_stats["avg_symmetry"] else 0.0
    
    return {
        "session_duration_sec": round(duration, 1),
        "embeddings_added_per_student": session_stats["embeddings_added"],
        "total_embeddings_added": sum(session_stats["embeddings_added"].values()),
        "rejected_by_symmetry_gate": session_stats["rejected_by_symmetry"],
        "rejected_by_score_gate": session_stats["rejected_by_score"],
        "avg_symmetry_of_accepted": round(avg_sym, 3),
        "poisoning_risk": "LOW" if avg_sym > 0.90 else "MEDIUM" if avg_sym > 0.85 else "HIGH"
    }
@app.post("/api/reset-session")
def reset_session():
    global session_stats
    session_stats = {
        "start_time": None,
        "embeddings_added": {},
        "rejected_by_symmetry": 0,
        "rejected_by_score": 0,
        "avg_symmetry": [],
    }
    return {"status": "Session counters reset"}
# =========================================================
# ENROLLMENT & VERIFICATION (RESTORED)
# =========================================================
class EnrollPayload(BaseModel):
    student_id: str
    student_name: str
    class_nbr: str
    images: dict  


@app.post("/api/enroll-face")
def enroll_face(payload: EnrollPayload):
    student_embeddings = []
    for angle, b64_list in payload.images.items():
        for b64_str in b64_list:
            if not b64_str: 
                continue
            try:
                if ',' in b64_str: 
                    b64_str = b64_str.split(',')[1]
                img = Image.open(io.BytesIO(base64.b64decode(b64_str))).convert('RGB')
                
                face_tensor = mtcnn(img)
                if face_tensor is not None:
                    with torch.no_grad():
                        emb = face_net(face_tensor.unsqueeze(0).to(device)).cpu().numpy()[0]
                    student_embeddings.append(emb.tolist())
            except Exception: 
                pass
            
    if len(student_embeddings) == 0: 
        raise HTTPException(status_code=400, detail="Could not detect a clear face.")
    
    # Save to PostgreSQL
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Clear old embeddings first (full re-enrollment)
        cur.execute('DELETE FROM "Att_FaceEmbeddings" WHERE "StudentID" = %s', (payload.student_id,))
        
        for i, emb in enumerate(student_embeddings[:8]):  # Cap at 8
            cur.execute('''
                INSERT INTO "Att_FaceEmbeddings" 
                ("StudentID", "Embedding", "QualityScore", "Symmetry")
                VALUES (%s, %s, %s, %s)
            ''', (payload.student_id, emb, 0.85, 0.80))
        
        conn.commit()
        cur.close()
        conn.close()
        
        print(f"☁️ Saved {len(student_embeddings[:8])} embeddings for {payload.student_name} to PostgreSQL")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
    # Update runtime cache
    global_face_db[payload.student_id] = {
        "name": payload.student_name, 
        "embeddings": student_embeddings[:8]
    }
    
    return {
        "status": "success", 
        "message": f"Successfully memorized {payload.student_name}"
    }

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
session_stats = {
    "start_time": None,
    "embeddings_added": {},      # sid -> count
    "rejected_by_symmetry": 0,   # how many bad angles were blocked
    "rejected_by_score": 0,      # how many bad matches were blocked
    "avg_symmetry": [],          # list of symmetry scores that PASSED
}
next_track_id = 1
RECOGNITION_THRESHOLD = 0.65





def recognize_face(emb):
    best_score = -1.0
    best_id, best_name = None, "Unknown"
    
    for sid, data in global_face_db.items():
        for saved in data["embeddings"]:
            sim = cosine_similarity(emb, saved)
            if sim > best_score:
                best_score, best_id, best_name = sim, sid, data["name"]
    
    MATCH_THRESHOLD = 0.65
    if best_score >= MATCH_THRESHOLD:
        return best_id, best_name, best_score
    return None, "Unknown", best_score



def process_frame(image_b64):
    global live_tracker_memory
    t_start = time.perf_counter()
    
    boxes = []
    track_ids = []

    # --- PROFILING STATE ---
    timers = {}
    facenet_runs = 0
    yunet_detect_runs = 0
    yunet_extract_runs = 0
    db_compare_count = 0
    process = psutil.Process(os.getpid())
    mem_before = process.memory_info().rss / 1024 / 1024
    # -----------------------

    if not image_b64:
        return []
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]
    if not image_b64:
        return []

    t0 = time.perf_counter()
    try:
        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return []

    frame_bgr = np.array(img)[:, :, ::-1]
    
    timers['decode'] = (time.perf_counter() - t0) * 1000
    
    t1 = time.perf_counter()
    results = yolo_person.track(
        frame_bgr,
        conf=0.45,
        iou=0.40,
        classes=[0],
        tracker="bytetrack.yaml",
        persist=True,
        verbose=False
    )
    timers['yolo'] = (time.perf_counter() - t1) * 1000
    
    faces_out = []
    current_frame_tracks = {}
    pg_save_required = False  # For active learning PostgreSQL save
    current_time = time.time()

    if results and results[0].boxes is not None and results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        track_ids = results[0].boxes.id.cpu().numpy()

        for box, track_id in zip(boxes, track_ids):
            if np.isnan(box).any():
                continue
                
            x1, y1, x2, y2 = map(int, box)
            track_id = int(track_id)
            
            if (x2 - x1) < 40 or (y2 - y1) < 40:
                continue

            person = live_tracker_memory.get(track_id, {
                "student_id": None,
                "name": "Scanning...",
                "status": "scanning",
                "frames_no_face": 0,
                "last_seen": current_time,
                "last_recognized": 0,
                "last_processed": 0,
                "last_face_seen": 0
            })
            person["last_seen"] = current_time

            # FAST PATH 1: Known — skip recognition for 10 seconds
            if (person.get("status") == "known" and 
                (current_time - person.get("last_recognized", 0)) < 10.0):
                
                if (current_time - person.get("last_face_seen", 0)) > 3.0:
                    person["status"] = "no_face"
                    person["name"] = "No Face"
                    person["frames_no_face"] = 3
                
                current_frame_tracks[track_id] = person
                faces_out.append({
                    "box": [x1, y1, x2 - x1, y2 - y1],
                    "track_id": track_id,
                    "student_id": person["student_id"] if person["status"] == "known" else None,
                    "name": person["name"],
                    "status": person["status"]
                })
                continue

            # FAST PATH 2: Unknown
            if (person.get("status") == "unknown" and 
                (current_time - person.get("last_processed", 0)) < 5.0):
                current_frame_tracks[track_id] = person
                faces_out.append({
                    "box": [x1, y1, x2 - x1, y2 - y1],
                    "track_id": track_id,
                    "student_id": None,
                    "name": "Unknown",
                    "status": "unknown"
                })
                continue

            # FAST PATH 3: No face
            if (person.get("status") == "no_face" and 
                (current_time - person.get("last_processed", 0)) < 3.0):
                current_frame_tracks[track_id] = person
                faces_out.append({
                    "box": [x1, y1, x2 - x1, y2 - y1],
                    "track_id": track_id,
                    "student_id": None,
                    "name": "No Face",
                    "status": "no_face"
                })
                continue

            # --- HEAD CROP ---
            t_crop = time.perf_counter()
            head_h = int((y2 - y1) * 0.50)
            hx1 = max(0, x1 - 20)
            hy1 = max(0, y1 - 20)
            hx2 = min(img.width, x2 + 20)
            hy2 = min(img.height, y1 + head_h + 20)
            
            if hx2 <= hx1 or hy2 <= hy1:
                continue
            head_crop = img.crop((hx1, hy1, hx2, hy2))
            timers['crop'] = timers.get('crop', 0) + (time.perf_counter() - t_crop) * 1000

            # --- YUNET DETECT ---
            t_yunet = time.perf_counter()
            f_boxes, f_probs, f_landmarks = mtcnn.detect(head_crop, landmarks=True)
            timers['yunet_detect'] = timers.get('yunet_detect', 0) + (time.perf_counter() - t_yunet) * 1000
            yunet_detect_runs += 1
            
            face_found = False
            face_abs_box = None
            
            if f_boxes is not None and len(f_boxes) > 0 and f_boxes[0] is not None:
                boxes_raw = f_boxes[0]
                probs_raw = f_probs[0]
                landmarks_raw = f_landmarks[0]
                
                boxes_arr = np.array(boxes_raw)
                if boxes_arr.ndim == 1:
                    boxes_arr = boxes_arr.reshape(1, 4)
                
                probs_arr = np.array(probs_raw)
                if probs_arr.ndim == 0:
                    probs_arr = probs_arr.reshape(1)
                
                landmarks_arr = np.array(landmarks_raw)
                if landmarks_arr.ndim == 2:
                    landmarks_arr = landmarks_arr.reshape(1, 5, 2)
                
                if len(boxes_arr) > 0:
                    best_idx = int(np.argmax(probs_arr))
                    
                    if probs_arr[best_idx] > 0.80:
                        face_found = True
                        person["last_face_seen"] = current_time 
                        fb = boxes_arr[best_idx]
                        face_abs_box = [
                            int(hx1 + fb[0]),
                            int(hy1 + fb[1]),
                            int(fb[2] - fb[0]),
                            int(fb[3] - fb[1])
                        ]
                        person["frames_no_face"] = 0

                        best_box = boxes_arr[best_idx:best_idx+1]
                        
                        # --- YUNET EXTRACT ---
                        t_extract = time.perf_counter()
                        extracted = mtcnn.extract(head_crop, best_box, save_path=None)
                        timers['yunet_extract'] = timers.get('yunet_extract', 0) + (time.perf_counter() - t_extract) * 1000
                        yunet_extract_runs += 1
                        
                        if extracted is None:
                            face_tensors = []
                        elif isinstance(extracted, torch.Tensor):
                            face_tensors = [extracted]
                        else:
                            face_tensors = extracted
                        
                        if len(face_tensors) > 0:
                            face_tensor = face_tensors[0]
                            facenet_runs += 1
                            
                            # --- FACENET EMBEDDING ---
                            t_facenet = time.perf_counter()
                            with torch.no_grad():
                                emb = face_net(face_tensor.unsqueeze(0).to(device)).cpu().numpy()[0]
                            timers['facenet'] = timers.get('facenet', 0) + (time.perf_counter() - t_facenet) * 1000
                            
                            # --- DB SEARCH ---
                            t_db = time.perf_counter()
                            sid, name, score = recognize_face(emb)
                            db_compare_count += sum(len(v["embeddings"]) for v in global_face_db.values())
                            timers['db_search'] = timers.get('db_search', 0) + (time.perf_counter() - t_db) * 1000

                            if sid:
                                person["student_id"] = sid
                                person["name"] = name
                                person["status"] = "known"
                                person["last_recognized"] = current_time
                                
                                existing = global_face_db[sid]["embeddings"]
                                
                                # ACTIVE LEARNING: Save good new angles to PostgreSQL
                                if (0.72 < score < 0.94 and 
                                    probs_arr[best_idx] > 0.85 and
                                    len(existing) < 8):
                                    
                                    if len(existing) > 0:
                                        sims_to_existing = [cosine_similarity(emb.tolist(), e) for e in existing]
                                        if max(sims_to_existing) > 0.85:
                                            pass  # Too similar, skip
                                        else:
                                            lm = landmarks_arr[best_idx]
                                            left_eye, right_eye, nose = lm[0], lm[1], lm[2]
                                            dist_left = np.linalg.norm(nose - left_eye)
                                            dist_right = np.linalg.norm(nose - right_eye)
                                            symmetry = min(dist_left, dist_right) / (max(dist_left, dist_right) + 1e-6)
                                            
                                            if symmetry > 0.60:
                                                # Add to runtime cache
                                                existing.append(emb.tolist())
                                                pg_save_required = True  # Mark for PostgreSQL save
                                                
                                                session_stats["embeddings_added"][sid] = session_stats["embeddings_added"].get(sid, 0) + 1
                                                session_stats["avg_symmetry"].append(symmetry)
                                                if session_stats["start_time"] is None:
                                                    session_stats["start_time"] = time.time()
                                                
                                                print(f"🧠 ACTIVE LEARN: New angle for {name} (sym: {symmetry:.2f}, count: {len(existing)})")
                                            else:
                                                session_stats["rejected_by_symmetry"] += 1
                                    else:
                                        pass
                                else:
                                    if not (0.72 < score < 0.94) and len(existing) < 8:
                                        session_stats["rejected_by_score"] += 1
                            else:
                                person["student_id"] = None
                                person["name"] = "Unknown"
                                person["status"] = "unknown"

            if not face_found:
                person["frames_no_face"] += 1
                if person["frames_no_face"] > 3:
                    person["student_id"] = None
                    person["name"] = "No Face"
                    person["status"] = "no_face"
            person["last_processed"] = current_time
            current_frame_tracks[track_id] = person

            if person["status"] != "no_face":
                face_out = {
                    "box": [x1, y1, x2 - x1, y2 - y1],
                    "track_id": track_id,
                    "student_id": person["student_id"],
                    "name": person["name"],
                    "status": person["status"]
                }
                if face_abs_box:
                    face_out["face_box"] = face_abs_box
                faces_out.append(face_out)

    for tid, tdata in current_frame_tracks.items():
        live_tracker_memory[tid] = tdata
    
    live_tracker_memory = {
        tid: tdata for tid, tdata in live_tracker_memory.items()
        if (current_time - tdata.get("last_seen", 0)) < 10.0
    }

    # ☁️ ACTIVE LEARNING: Save new angles to PostgreSQL (batch save)
    if pg_save_required:
        t_save = time.perf_counter()
        try:
            conn = get_db()
            cur = conn.cursor()
            
            for sid, data in global_face_db.items():
                # Get current PG count
                cur.execute('SELECT COUNT(*) FROM "Att_FaceEmbeddings" WHERE "StudentID" = %s', (sid,))
                pg_count = cur.fetchone()[0]
                
                # Get embeddings that are in runtime cache but might not be in PG
                # For simplicity, we check the last added one
                runtime_embs = data["embeddings"]
                if len(runtime_embs) > pg_count and pg_count < 8:
                    # Save the newest embedding
                    newest_emb = runtime_embs[-1]
                    cur.execute('''
                        INSERT INTO "Att_FaceEmbeddings" 
                        ("StudentID", "Embedding", "QualityScore", "Symmetry")
                        VALUES (%s, %s, %s, %s)
                    ''', (sid, newest_emb, 0.85, 0.80))
                    print(f"☁️ Active learning: saved new angle for {data['name']} to PostgreSQL")
            
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"⚠️ Failed to save active learning to PostgreSQL: {e}")
        
        timers['db_save'] = (time.perf_counter() - t_save) * 1000

    # --- PROFILING REPORT ---
    total = (time.perf_counter() - t_start) * 1000
    mem_after = process.memory_info().rss / 1024 / 1024
    mem_delta = mem_after - mem_before
    
    if not hasattr(process_frame, "_frame_counter"):
        process_frame._frame_counter = 0
    process_frame._frame_counter += 1
    
    if facenet_runs > 0 or process_frame._frame_counter % 30 == 0:
        print(f"\n{'='*60}")
        print(f"📊 FRAME {process_frame._frame_counter} | People:{len(boxes)} | Total:{total:.1f}ms")
        print(f"   Base64/Decode : {timers.get('decode',0):>6.1f}ms")
        print(f"   YOLO Track    : {timers.get('yolo',0):>6.1f}ms")
        print(f"   Head Crop     : {timers.get('crop',0):>6.1f}ms")
        print(f"   YuNet Detect  : {timers.get('yunet_detect',0):>6.1f}ms ({yunet_detect_runs}x)")
        print(f"   YuNet Extract : {timers.get('yunet_extract',0):>6.1f}ms ({yunet_extract_runs}x)")
        print(f"   🔴 FaceNet    : {timers.get('facenet',0):>6.1f}ms ({facenet_runs}x)")
        print(f"   🔴 DB Search   : {timers.get('db_search',0):>6.1f}ms ({db_compare_count} comparisons)")
        print(f"   DB Save       : {timers.get('db_save',0):>6.1f}ms")
        print(f"   Memory        : {mem_after:.0f}MB (Δ{mem_delta:+.0f}MB)")
        print(f"{'='*60}")

    return faces_out

@app.websocket("/ws/surveillance")
async def ws_surveillance(ws: WebSocket):
    await ws.accept()
    global live_tracker_memory
    live_tracker_memory.clear()
    
    try:
        while True:
            data = await ws.receive_json()
            faces = await asyncio.to_thread(process_frame, data["image"])
            await ws.send_json({"status": "success", "faces": faces})
    except WebSocketDisconnect:
        pass  # Normal disconnect
    except Exception as e:
        print(f"⚠️ WebSocket error: {e}")
    finally:
        live_tracker_memory.clear()
        # Suppress Windows Proactor error on connection close
        try:
            await ws.close()
        except Exception:
            pass

# =========================================================
# QUICK ASSIGN (TEACHER CLICK)
# =========================================================
class AssignPayload(BaseModel):
    student_id: str
    student_name: str
    image: str
    box: Optional[list] = None
    is_manual: bool = False
    class_nbr: Optional[str] = None
    
@app.post("/api/assign-face")
def assign_face(p: AssignPayload):
    if "," in p.image: 
        p.image = p.image.split(",")[1]
    
    try:
        img = Image.open(io.BytesIO(base64.b64decode(p.image))).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data.")
    
    # MANUAL ZOOM: image is already a magnified face crop
    if p.is_manual or not p.box:
        if max(img.size) > 1200:
            img.thumbnail((800, 800))
        head_crop = img
    else:
        # YOLO TRACKING: existing body-box to head-crop logic
        x, y, w, h = map(int, p.box)
        head_h = int(h * 0.50)
        hx1 = max(0, x - 20)
        hy1 = max(0, y - 20)
        hx2 = min(img.width, x + w + 20)
        hy2 = min(img.height, y + head_h + 20)
        
        if hx2 <= hx1 or hy2 <= hy1:
            raise HTTPException(status_code=400, detail="Invalid face region.")
        
        head_crop = img.crop((hx1, hy1, hx2, hy2))
    
    # --- YUNET DETECTION ---
    f_boxes, f_probs, f_landmarks = mtcnn.detect(head_crop, landmarks=True)
    
    if f_boxes is None or len(f_boxes) == 0 or f_boxes[0] is None:
        raise HTTPException(status_code=400, detail="No face detected. Ask student to look at camera.")
    
    boxes_raw = f_boxes[0]
    probs_raw = f_probs[0]
    landmarks_raw = f_landmarks[0]
    
    boxes_arr = np.array(boxes_raw)
    if boxes_arr.ndim == 1: 
        boxes_arr = boxes_arr.reshape(1, 4)
    probs_arr = np.array(probs_raw)
    if probs_arr.ndim == 0: 
        probs_arr = probs_arr.reshape(1)
    landmarks_arr = np.array(landmarks_raw)
    if landmarks_arr.ndim == 2: 
        landmarks_arr = landmarks_arr.reshape(1, 5, 2)
    
    # MANUAL ZOOM: Reject if multiple faces in crop
    if p.is_manual and len(boxes_arr) > 1:
        high_conf_count = sum(1 for prob in probs_arr if prob > 0.90)
        if high_conf_count > 1:
            raise HTTPException(
                status_code=400, 
                detail=f"Multiple faces detected in zoom ({high_conf_count} found). Please zoom closer on one student only."
            )
    
    best_idx = int(np.argmax(probs_arr))
    
    # QUALITY GATES
    if probs_arr[best_idx] < 0.80:
        raise HTTPException(status_code=400, detail=f"Face too unclear ({probs_arr[best_idx]:.2f}). Ask student to look directly at camera.")
    
    # Frontal check
    lm = landmarks_arr[best_idx]
    left_eye, right_eye, nose = lm[0], lm[1], lm[2]
    dist_left = np.linalg.norm(nose - left_eye)
    dist_right = np.linalg.norm(nose - right_eye)
    symmetry = min(dist_left, dist_right) / (max(dist_left, dist_right) + 1e-6)
    
    if symmetry < 0.70:
        raise HTTPException(status_code=400, detail=f"Face not frontal enough ({symmetry:.2f}). Ask student to face camera.")
    
    # Extract embedding
    best_box = boxes_arr[best_idx:best_idx+1]
    extracted = mtcnn.extract(head_crop, best_box, save_path=None)
    
    if extracted is None:
        face_tensors = []
    elif isinstance(extracted, torch.Tensor):
        face_tensors = [extracted]
    else:
        face_tensors = extracted
    
    if len(face_tensors) == 0:
        raise HTTPException(status_code=400, detail="Face extraction failed.")
    
    face_tensor = face_tensors[0]
    with torch.no_grad():
        emb = face_net(face_tensor.unsqueeze(0).to(device)).cpu().numpy()[0]
    
    # IDENTITY CONSISTENCY
    if p.student_id in global_face_db and len(global_face_db[p.student_id]["embeddings"]) > 0:
        existing_embs = global_face_db[p.student_id]["embeddings"]
        sims_to_self = [cosine_similarity(emb.tolist(), e) for e in existing_embs]
        max_self_sim = max(sims_to_self)
        if max_self_sim < 0.55:
            return {
                "status": "error",
                "message": f"🚫 IDENTITY MISMATCH: This face does not match existing biometric record for {global_face_db[p.student_id]['name']}.",
                "max_similarity_to_record": round(max_self_sim, 3)
            }
    
    # DUPLICATE FACE CHECK
    DUPLICATE_THRESHOLD = 0.75
    for existing_id, existing_data in global_face_db.items():
        if existing_id == p.student_id:
            continue
        for saved_emb in existing_data["embeddings"]:
            sim = cosine_similarity(emb.tolist(), saved_emb)
            if sim > DUPLICATE_THRESHOLD:
                return {
                    "status": "error",
                    "message": f"🚫 DUPLICATE FACE: This face already belongs to {existing_data['name']} (ID: {existing_id}).",
                    "existing_student_id": existing_id,
                    "existing_name": existing_data["name"],
                    "similarity": round(sim, 3)
                }
    
    # ☁️ SAVE TO POSTGRESQL ONLY
    conn = None
    cur = None
    count = 0  # ← DEFINED HERE for use in return statement
    
    try:
        conn = get_db()
        cur = conn.cursor()
        
        # Count existing embeddings
        cur.execute('SELECT COUNT(*) FROM "Att_FaceEmbeddings" WHERE "StudentID" = %s', (p.student_id,))
        count = cur.fetchone()[0]
        
        if count >= 8:
            return {
                "status": "success",
                "message": f"{p.student_name} already has maximum biometric data (8 angles).",
                "quality": "maxed",
                "total_embeddings": count
            }
        
        # Check redundancy
        cur.execute('SELECT "Embedding" FROM "Att_FaceEmbeddings" WHERE "StudentID" = %s', (p.student_id,))
        existing_rows = cur.fetchall()
        
        for (existing_emb,) in existing_rows:
            if cosine_similarity(emb.tolist(), existing_emb) > 0.90:
                return {
                    "status": "success",
                    "message": f"{p.student_name} already enrolled with similar angle.",
                    "quality": "redundant",
                    "total_embeddings": count
                }
        
        # Insert new embedding
        cur.execute('''
            INSERT INTO "Att_FaceEmbeddings" 
            ("StudentID", "Embedding", "QualityScore", "Symmetry", "SourceClassNbr")
            VALUES (%s, %s, %s, %s, %s)
        ''', (p.student_id, emb.tolist(), float(probs_arr[best_idx]), symmetry, p.class_nbr))
        
        conn.commit()
        print(f"☁️ Saved embedding {count+1}/8 for {p.student_name} to PostgreSQL")
        
    except Exception as e:
        if cur: cur.close()
        if conn: conn.close()
        print(f"⚠️ Failed to save to PostgreSQL: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        if cur: cur.close()
        if conn: conn.close()
    
    # Update runtime cache immediately (so next frame recognizes)
    if p.student_id not in global_face_db:
        global_face_db[p.student_id] = {"name": p.student_name, "embeddings": []}
    
    global_face_db[p.student_id]["embeddings"].append(emb.tolist())
    
    # Cap runtime cache to 8
    if len(global_face_db[p.student_id]["embeddings"]) > 8:
        global_face_db[p.student_id]["embeddings"] = global_face_db[p.student_id]["embeddings"][:8]
    
    # Clear tracker so next frame recognizes immediately
    global live_tracker_memory
    live_tracker_memory.clear()
    
    return {
        "status": "success",
        "message": f"✅ {p.student_name} enrolled successfully.",
        "quality": "excellent",
        "symmetry": round(symmetry, 3),
        "confidence": round(float(probs_arr[best_idx]), 3),
        "total_embeddings": count + 1  # ← count is now defined
    }




class UnassignPayload(BaseModel):
    student_id: str

@app.post("/api/unassign-student")
def unassign_student(p: UnassignPayload):
    # Remove from PostgreSQL
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('DELETE FROM "Att_FaceEmbeddings" WHERE "StudentID" = %s', (p.student_id,))
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        print(f"🗑️ Deleted {deleted} embeddings for {p.student_id} from PostgreSQL")
    except Exception as e:
        print(f"⚠️ Failed to delete from PostgreSQL: {e}")
    
    # Remove from runtime cache
    if p.student_id in global_face_db:
        del global_face_db[p.student_id]
    
    # Clear tracker
    global live_tracker_memory
    live_tracker_memory.clear()
    
    return {
        "status": "success", 
        "message": f"Student unassigned. Biometric data removed from database.",
        "student_id": p.student_id
    }
# =========================================================
# EXCEL EXPORT (RESTORED)
# =========================================================
class AttendanceExportPayload(BaseModel):
    class_nbr: int
    attendance_records: Dict[str, str]

@app.post("/api/export-attendance")
def export_attendance(payload: AttendanceExportPayload):
    conn = get_db()
    cur = conn.cursor()
    
    # Get the students for this class and the course info
    cur.execute('''
        SELECT s."StudentID", s."StudentName", c."CourseName", c."StartTime"
        FROM "Att_Student" s
        JOIN "Att_Class_List" cl ON s."StudentID" = cl."StudentID"
        JOIN "Att_Course_Class" c ON cl."ClassNbr" = c."ClassNbr"
        WHERE cl."ClassNbr" = %s
    ''', (str(payload.class_nbr),))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    if not rows:
        raise HTTPException(status_code=404, detail="Class not found.")
        
    # Build the report data
    report_data = []
    for row in rows:
        student_id, student_name, course_name, start_time = row
        # Check the payload to see if they were marked present
        status = "Present" if payload.attendance_records.get(str(student_id)) == "present" else "Absent"
        
        report_data.append({
            "Student ID": student_id,
            "Student Name": student_name,
            "Course Name": course_name,
            "Start Time": start_time.strftime("%I:%M %p") if start_time else "",
            "Attendance Status": status
        })

    # Convert to DataFrame just for easy Excel exporting
    import pandas as pd
    report_df = pd.DataFrame(report_data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        report_df.to_excel(writer, index=False, sheet_name='Attendance Report')
    output.seek(0)
    
    headers = { 'Content-Disposition': f'attachment; filename="Attendance_Class_{payload.class_nbr}.xlsx"' }
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
@app.get("/api/debug-routes")
def debug_routes():
    routes = []
    for route in app.routes:
        if hasattr(route, "methods"):
            routes.append({
                "path": route.path,
                "methods": list(route.methods),
                "name": route.name
            })
    return {"routes": routes, "total": len(routes)}
# =========================================================
# FRONTEND
# =========================================================
if os.path.exists("frontend/dist"):
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)