import os
os.environ["OPENVINO_DEVICE"] = "GPU"

# 🎯 MONKEY-PATCH: Intercept OpenVINO compile_model and force GPU
import openvino as ov
_original_compile_model = ov.Core.compile_model

def _patched_compile_model(self, model, device_name=None, config=None):
    # 🎯 FIX: Force YOLO to stay on the CPU!
    print(f"🔄 OpenVINO device override: {device_name} → CPU")
    device_name = "CPU"
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
import pyodbc
import numpy as np
from fastapi.responses import FileResponse
import torch
from PIL import Image
import io
import asyncio
from ultralytics import YOLO
from insightface.app import FaceAnalysis
import cv2
from typing import Dict
import time
from typing import Optional
from fastapi import UploadFile, File
from fastapi import Request
import psycopg2
from pgvector.psycopg2 import register_vector
import threading
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="insightface")

import torch._dynamo
torch._dynamo.config.suppress_errors = True
# Optional: stop the noisy atexit traceback on Ctrl+C
import atexit, torch._dynamo.utils as _du
if hasattr(_du, 'dump_compile_times'):
    atexit.unregister(_du.dump_compile_times)
# =========================================================
# APP SETUP
# =========================================================
app = FastAPI(title="AI Live Attendance Backend")
app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"],      # 🚦 Back to standard wildcard
    allow_credentials=False,  # 🚦 THIS IS THE FIX: Disables strict WebSocket blocking!
    allow_methods=["*"], 
    allow_headers=["*"],
    expose_headers=["X-QR-Token"]
)

from concurrent.futures import ThreadPoolExecutor
_prev_person_count = 0
_zoom_recovery_until = 0.0
# Create a thread pool so heavy AI processing doesn't block the async event loop
ws_executor = ThreadPoolExecutor(max_workers=1)

@app.websocket("/ws/surveillance")
async def websocket_surveillance(websocket: WebSocket):
    await websocket.accept()
    print("🔌 WebSocket surveillance client connected")
    try:
        while True:
            data = await websocket.receive_json()
            image_b64 = data.get("image", "")
            
            try:
                faces = await asyncio.get_event_loop().run_in_executor(
                    ws_executor, process_frame, image_b64
                )
                await websocket.send_json({"faces": faces})
            except Exception as e:
                print(f"⚠️ Frame processing error (skipped): {e}")
                await websocket.send_json({"faces": []})
            
    except WebSocketDisconnect:
        print("🔌 WebSocket client disconnected")
    except Exception as e:
        print(f"⚠️ WebSocket transport error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

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

print("🔹 Loading InsightFace on Intel iGPU (OpenVINO EP)...")
face_app = FaceAnalysis(
    name='buffalo_s',
    allowed_modules=['detection', 'recognition'],
    providers=['OpenVINOExecutionProvider', 'CPUExecutionProvider'],
    provider_options=[{'device_type': 'GPU'}]  # Intel iGPU
)
face_app.prepare(ctx_id=0, det_size=(160, 160))
ai_lock = threading.Lock()
print("✅ InsightFace loaded on Intel GPU via OpenVINO EP")



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# =========================================================
# 🔐 BIO-HASHING (ORTHOGONAL RANDOM PROJECTION)
# =========================================================
SECRET_MATRIX_PATH = os.path.join(DATA_DIR, "secret_matrix.npy")

def get_or_create_secret_matrix(dim=512):
    """Loads or generates a mathematical lock that scrambles Face DNA."""
    if os.path.exists(SECRET_MATRIX_PATH):
        return np.load(SECRET_MATRIX_PATH)
    else:
        print("🔐 Generating new Bio-Hashing Secret Matrix...")
        # 1. Generate a random matrix
        H = np.random.randn(dim, dim)
        # 2. QR decomposition extracts a perfectly Orthogonal Matrix (Q)
        Q, R = np.linalg.qr(H)
        Q = Q.astype(np.float32) # Ensure it matches FaceNet data type
        np.save(SECRET_MATRIX_PATH, Q)
        return Q

SECRET_MATRIX = get_or_create_secret_matrix(512)

def get_face_embedding(raw_emb):
    """Applies Bio-Hashing encryption to the raw ArcFace embedding."""
    scrambled_emb = np.dot(raw_emb, SECRET_MATRIX)
    return scrambled_emb

global_face_db = {}

def cosine_similarity(a, b):
    a, b = np.array(a).flatten(), np.array(b).flatten()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
# --- POSTGRESQL DATABASE CONFIGURATION ---
PG_DB_CONFIG = {
    "dbname": "liwa_attendance",
    "user": "postgres",
    "password": "aze123",  # <--- Put your Postgres password here
    "host": "localhost",
    "port": "5432"
}

def get_pg_db():
    conn = psycopg2.connect(**PG_DB_CONFIG)
    register_vector(conn)
    return conn

# --- 2. MICROSOFT SQL SERVER CONFIGURATION (Rosters & Students) ---
# "Trusted_Connection=yes" means it uses your Windows login, no password needed!
MS_SQL_CONFIG = (
    "Driver={SQL Server};"
    "Server=localhost\\SQLEXPRESS;"
    "Database=liwa_attendance;"
    "Trusted_Connection=yes;"
)

def get_ms_db():
    return pyodbc.connect(MS_SQL_CONFIG)



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
    
    # 🎯 NEW: Check the Microsoft SQL Server for the teacher!
    try:
        conn = get_ms_db()
        cur = conn.cursor()
        cur.execute('SELECT FacultyName FROM Att_Course_Class WHERE LOWER(FacultyID) = LOWER(?)', (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        raise HTTPException(500, f"MS SQL Database error: {str(e)}")
    
    if not user:
        raise HTTPException(404, "Faculty email not found in university database")
    
    verify_token = secrets.token_urlsafe(32)
    session["status"] = "email_entered"
    session["email"] = email
    session["faculty_name"] = user[0] 
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
    conn = get_ms_db()
    cur = conn.cursor()
    cur.execute('SELECT FacultyName FROM Att_Course_Class WHERE LOWER(FacultyID) = LOWER(?)', (email,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    
    if not user: 
        raise HTTPException(404, "Faculty email not found in database")
    return {"status": "success", "name": user[0]}

@app.get("/api/classes")
def classes(email: str, room_id: str = None):
    conn = get_ms_db()
    cur = conn.cursor()
    
    query = '''SELECT ClassNbr, sTerm AS Semester, Code AS [Course Code], 
               CourseName AS [Course Name], StartTime AS [Start Time], RoomID AS [Room ID] 
               FROM Att_Course_Class WHERE LOWER(FacultyID) = LOWER(?)'''
    params = [email]
    
    if room_id:
        query += ' AND RoomID = ?'
        params.append(room_id)
        
    cur.execute(query, params)
    columns = [column[0] for column in cur.description]
    results = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    
    for r in results:
        if r['Start Time']:
            r['Start Time'] = r['Start Time'].strftime("%I:%M %p")
    return results

@app.get("/api/students")
def students(email: str, class_nbr: str):
    # 1. Get Names and IDs from Microsoft SQL Server
    conn_ms = get_ms_db()
    cur_ms = conn_ms.cursor()
    cur_ms.execute('''
        SELECT s.StudentID, s.StudentName
        FROM Att_Student s
        JOIN Att_Class_List cl ON s.StudentID = cl.StudentID
        WHERE cl.ClassNbr = ?
    ''', (str(class_nbr),))
    
    ms_rows = cur_ms.fetchall()
    student_list = [{"Student ID": row[0], "Student Name": row[1]} for row in ms_rows]
    student_ids = [row[0] for row in ms_rows]
    cur_ms.close()
    conn_ms.close()

    # 2. Get Face DNA from PostgreSQL
    global global_face_db
    global_face_db.clear()
    
    if student_ids:
        conn_pg = get_pg_db()
        cur_pg = conn_pg.cursor()
        cur_pg.execute('''
            SELECT "StudentID", "Embedding"
            FROM "Att_FaceEmbeddings"
            WHERE "StudentID" = ANY(%s)
        ''', (student_ids,))
        
        pg_rows = cur_pg.fetchall()
        
        from collections import defaultdict
        embs_by_student = defaultdict(list)
        for sid, emb in pg_rows:
            if emb is not None:
                embs_by_student[sid].append(emb)
                
        # Load AI Memory
        for student in student_list:
            sid = student["Student ID"]
            global_face_db[sid] = {
                "name": student["Student Name"],
                "embeddings": embs_by_student.get(sid, [])
            }
            
        cur_pg.close()
        conn_pg.close()
        
    print(f"🧠 AI Memory loaded with {len(global_face_db)} known student records for class {class_nbr}.")
    return student_list

@app.get("/api/db-health")
def db_health():
    """Query PostgreSQL directly for embedding quality."""
    report = []
    
    try:
        conn = get_pg_db()
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
            if not b64_str: continue
            try:
                if ',' in b64_str: b64_str = b64_str.split(',')[1]
                img = Image.open(io.BytesIO(base64.b64decode(b64_str))).convert('RGB')
                img_bgr = np.array(img)[:, :, ::-1] # InsightFace needs BGR
                
                faces = safe_face_detect(img_bgr)
                    
                if faces:
                    best_face = sorted(faces, key=lambda x: x.det_score, reverse=True)[0]
                    emb = get_face_embedding(best_face.embedding)
                    student_embeddings.append(emb.tolist())
            except Exception: pass
            
    if len(student_embeddings) == 0: 
        raise HTTPException(status_code=400, detail="Could not detect a clear face.")
    
    # Save to PostgreSQL
    try:
        conn = get_pg_db()
        cur = conn.cursor()
        cur.execute('DELETE FROM "Att_FaceEmbeddings" WHERE "StudentID" = %s', (payload.student_id,))
        for i, emb in enumerate(student_embeddings[:15]):  # Cap increased to 15!
            cur.execute('''INSERT INTO "Att_FaceEmbeddings" ("StudentID", "Embedding", "QualityScore", "Symmetry")
                           VALUES (%s, %s, %s, %s)''', (payload.student_id, emb, 0.85, 0.80))
        conn.commit()
        cur.close()
        conn.close()
        print(f"☁️ Saved {len(student_embeddings[:15])} ArcFace embeddings for {payload.student_name}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
    global_face_db[payload.student_id] = {"name": payload.student_name, "embeddings": student_embeddings[:15]}
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
        img_bgr = np.array(img)[:, :, ::-1]
        
        faces = safe_face_detect(img_bgr)
            
        if not faces: raise ValueError("No face detected")
        live_embedding = get_face_embedding(faces[0].embedding)
    except Exception: raise HTTPException(status_code=400, detail="No face detected in the camera.")

    best_match_name, best_match_score = "Unknown", -1.0 
    MATCH_THRESHOLD = 0.40 # ArcFace optimized threshold

    for student_id, data in global_face_db.items():
        for saved_embedding in data["embeddings"]:
            sim = cosine_similarity(live_embedding, saved_embedding)
            if sim > best_match_score:
                best_match_score = sim
                best_match_name = data["name"]

    if best_match_score > MATCH_THRESHOLD:
        return {"status": "success", "match": True, "name": best_match_name, "confidence": f"{best_match_score*100:.1f}%"}
    return {"status": "success", "match": False, "name": "Unknown"}

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
    
    MATCH_THRESHOLD = 0.40
    if best_score >= MATCH_THRESHOLD:
        return best_id, best_name, best_score
    return None, "Unknown", best_score


def _pad_to_32(img_bgr):
    """DirectML workaround: pad so H and W are multiples of 32."""
    h, w = img_bgr.shape[:2]
    new_h = ((h + 31) // 32) * 32
    new_w = ((w + 31) // 32) * 32
    if new_h == h and new_w == w:
        return img_bgr
    padded = np.full((new_h, new_w, 3), (114, 114, 114), dtype=np.uint8)  # gray pad
    padded[:h, :w] = img_bgr
    return padded

def safe_face_detect(img_bgr, det_thresh=0.5):
    """Wrapper that pads for DML and catches the Reshape bug gracefully."""
    if img_bgr is None or img_bgr.size == 0:
        return []
    h, w = img_bgr.shape[:2]
    if h < 32 or w < 32:
        return []  # too small anyway
    
    safe_img = _pad_to_32(img_bgr)
    
    try:
        faces = safe_face_detect(img_bgr)
    except Exception as e:
        err = str(e)
        if "Reshape" in err or "DmlExecutionProvider" in err or "80070057" in err:
            print(f"⚠️ DML bug on {w}x{h} crop, skipped.")
            return []
        raise


def process_frame(image_b64):
    global live_tracker_memory, _prev_person_count, _zoom_recovery_until
    t_start = time.perf_counter()

    if not image_b64: return []
    if "," in image_b64: image_b64 = image_b64.split(",")[1]
    if not image_b64: return []

    try:
        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return []

    frame_bgr = np.array(img)[:, :, ::-1]
    
    results = yolo_person.track(
        frame_bgr, conf=0.45, iou=0.40, classes=[0],
        tracker="bytetrack.yaml", persist=True, verbose=False
    )
    
    faces_out = []
    current_frame_tracks = {}
    pg_save_required = False  
    current_time = time.time()
    seen_sids_this_frame = set()

    # --- ZOOM DETECTION & THROTTLE ---
    current_count = 0
    if results and results[0].boxes is not None and results[0].boxes.id is not None:
        current_count = len(results[0].boxes)
        
        # Detect sudden zoom-out (e.g. 3 people → 12 people)
        if (_prev_person_count > 0 and 
            current_count >= _prev_person_count * 2 and 
            current_count >= 5):
            _zoom_recovery_until = current_time + 3.0
            print(f"🔍 Zoom-out burst: {current_count} people. Recovering...")
        _prev_person_count = current_count
        
        # We can safely process more faces now because buffalo_s is ultra-light!
        MAX_HEAVY_AI = 3
    else:
        MAX_HEAVY_AI = 2

    if results and results[0].boxes is not None and results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        track_ids = results[0].boxes.id.cpu().numpy()

        # PASS 1: Reserve known identities
        for box, track_id in zip(boxes, track_ids):
            person = live_tracker_memory.get(int(track_id))
            if person and person.get("status") == "known" and (current_time - person.get("last_recognized", 0)) < 10.0:
                if person.get("student_id"):
                    seen_sids_this_frame.add(person["student_id"])

        # PASS 2: Sort by box size — largest (closest) faces first
        box_data = list(zip(boxes, track_ids))
        box_data.sort(
            key=lambda x: (x[0][2] - x[0][0]) * (x[0][3] - x[0][1]),
            reverse=True
        )

        heavy_ai_runs_this_frame = 0

        for box, track_id in box_data:
            if np.isnan(box).any(): continue
            x1, y1, x2, y2 = map(int, box)
            track_id = int(track_id)
            if (x2 - x1) < 40 or (y2 - y1) < 40: continue

            person = live_tracker_memory.get(track_id, {
                "student_id": None, "name": "Scanning...", "status": "scanning",
                "match_score": 0.0, "frames_no_face": 0, "last_seen": current_time, 
                "last_recognized": 0, "last_processed": 0, "last_face_seen": 0
            })
            person["last_seen"] = current_time

            # FAST PATH 1: Known and fresh
            if person.get("status") == "known" and (current_time - person.get("last_recognized", 0)) < 10.0:
                if (current_time - person.get("last_face_seen", 0)) > 3.0:
                    person["status"] = "no_face"
                    person["name"] = "No Face"
                    person["frames_no_face"] = 3
                    if person.get("student_id") in seen_sids_this_frame:
                        seen_sids_this_frame.remove(person["student_id"])
                
                current_frame_tracks[track_id] = person
                faces_out.append({
                    "box": [x1, y1, x2 - x1, y2 - y1], 
                    "track_id": track_id, 
                    "student_id": person["student_id"] if person["status"] == "known" else None, 
                    "name": person["name"], 
                    "status": person["status"]
                })
                continue

                        # FAST PATH 2: Unknown retry — 1.5s normally, 0.3s during zoom-out burst
            unknown_lockout = 1.5
            if current_time < _zoom_recovery_until:
                unknown_lockout = 0.3
            
            if person.get("status") == "unknown" and (current_time - person.get("last_processed", 0)) < unknown_lockout:
                current_frame_tracks[track_id] = person
                faces_out.append({
                    "box": [x1, y1, x2 - x1, y2 - y1], 
                    "track_id": track_id, 
                    "student_id": None, 
                    "name": "Unknown", 
                    "status": "unknown"
                })
                continue

            # HEARTBEAT THROTTLE: Max 2 InsightFace runs per frame
            if heavy_ai_runs_this_frame >= MAX_HEAVY_AI:
                current_frame_tracks[track_id] = person
                faces_out.append({
                    "box": [x1, y1, x2 - x1, y2 - y1], 
                    "track_id": track_id, 
                    "student_id": None, 
                    "name": "Scanning...", 
                    "status": "scanning"
                })
                continue
            
            heavy_ai_runs_this_frame += 1

            # --- HEAD CROP ---
            head_h = int((y2 - y1) * 0.70)
            hx1, hy1 = max(0, x1 - 20), max(0, y1 - 20)
            hx2, hy2 = min(img.width, x2 + 20), min(img.height, y1 + head_h + 20)
            
            if hx2 <= hx1 or hy2 <= hy1: continue
            head_crop = img.crop((hx1, hy1, hx2, hy2))
            head_crop_bgr = np.array(head_crop)[:, :, ::-1]

            # --- INSIGHTFACE DETECT & EXTRACT ---
            
            detected_faces = safe_face_detect(head_crop_bgr)
            
            face_found = False
            
            if detected_faces and len(detected_faces) > 0:
                best_face = sorted(detected_faces, key=lambda x: x.det_score, reverse=True)[0]
                
                if best_face.det_score > 0.60: 
                    face_found = True
                    person["last_face_seen"] = current_time 
                    person["frames_no_face"] = 0
                    
                    raw_emb = best_face.embedding
                    emb = get_face_embedding(raw_emb)
                    
                    sid, name, score = recognize_face(emb)

                    # 🛑 IDENTITY STEALING (BIPARTITE MATCHING)
                    if sid:
                        for other_tid, other_data in list(live_tracker_memory.items()):
                            if other_tid != track_id and other_data.get("student_id") == sid:
                                if score > other_data.get("match_score", 0):
                                    other_data["student_id"] = None
                                    other_data["name"] = "Unknown"
                                    other_data["status"] = "unknown"
                                    other_data["match_score"] = 0
                                else:
                                    sid, name, score = None, "Unknown", -1.0
                                    break
                    
                    if sid:
                        person["student_id"] = sid
                        person["name"] = name
                        person["status"] = "known"
                        person["match_score"] = score
                        person["last_recognized"] = current_time
                        
                        existing = global_face_db[sid]["embeddings"]
                        
                        # ACTIVE LEARNING
                        if score < 0.85 and best_face.det_score > 0.85 and len(existing) < 15:
                            sims_to_existing = [cosine_similarity(emb.tolist(), e) for e in existing] if existing else [0]
                            if max(sims_to_existing) <= 0.80:
                                kps = best_face.kps
                                dist_left = np.linalg.norm(kps[2] - kps[0])
                                dist_right = np.linalg.norm(kps[2] - kps[1])
                                symmetry = min(dist_left, dist_right) / (max(dist_left, dist_right) + 1e-6)
                                
                                if symmetry > 0.60:
                                    existing.append(emb.tolist())
                                    pg_save_required = True 
                    else:
                        person["student_id"] = None
                        person["name"] = "Unknown"
                        person["status"] = "unknown"

            if not face_found:
                person["frames_no_face"] += 1
                if person["frames_no_face"] > 3:
                    person["status"] = "no_face"
                    person["name"] = "No Face"
                    
            person["last_processed"] = current_time
            current_frame_tracks[track_id] = person
            faces_out.append({
                "box": [x1, y1, x2 - x1, y2 - y1], 
                "track_id": track_id, 
                "student_id": person.get("student_id"), 
                "name": person.get("name"), 
                "status": person.get("status")
            })

    live_tracker_memory = {tid: tdata for tid, tdata in current_frame_tracks.items() 
                           if (current_time - tdata.get("last_seen", 0)) < 10.0}

    # ☁️ ACTIVE LEARNING SAVE
    if pg_save_required:
        try:
            conn = get_pg_db()
            cur = conn.cursor()
            for sid, data in global_face_db.items():
                cur.execute('SELECT COUNT(*) FROM "Att_FaceEmbeddings" WHERE "StudentID" = %s', (sid,))
                pg_count = cur.fetchone()[0]
                
                runtime_embs = data["embeddings"]
                if len(runtime_embs) > pg_count and pg_count < 15:
                    newest_emb = runtime_embs[-1]
                    cur.execute('''INSERT INTO "Att_FaceEmbeddings" ("StudentID", "Embedding", "QualityScore", "Symmetry") VALUES (%s, %s, %s, %s)''', (sid, newest_emb, 0.85, 0.80))
            conn.commit()
            cur.close()
            conn.close()
        except Exception: pass

    return faces_out

class AssignPayload(BaseModel):
    student_id: str
    student_name: str
    image: str
    box: Optional[list] = None
    is_manual: bool = False
    class_nbr: Optional[str] = None
    
@app.post("/api/assign-face")
def assign_face(p: AssignPayload):
    if "," in p.image: p.image = p.image.split(",")[1]
    
    try:
        img_bytes = base64.b64decode(p.image)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data.")
    
    if p.is_manual or not p.box:
        if max(img.size) > 1200: img.thumbnail((800, 800))
        head_crop = img
    else:
        x, y, w, h = map(int, p.box)
        head_h = int(h * 0.70)
        hx1, hy1 = max(0, x - 20), max(0, y - 20)
        hx2, hy2 = min(img.width, x + w + 20), min(img.height, y + head_h + 20)
        head_crop = img.crop((hx1, hy1, hx2, hy2))
    
    head_crop_bgr = np.array(head_crop)[:, :, ::-1]
    
    faces = safe_face_detect(head_crop_bgr)
        
    if not faces:
        raise HTTPException(status_code=400, detail="No face detected. Ask student to look at camera.")
    
    if p.is_manual and len(faces) > 1:
        raise HTTPException(status_code=400, detail=f"Multiple faces detected. Please zoom closer on one student.")
    
    best_face = sorted(faces, key=lambda x: x.det_score, reverse=True)[0]
    
    if best_face.det_score < 0.60:
        raise HTTPException(status_code=400, detail=f"Face too unclear ({best_face.det_score:.2f}).")
        
    kps = best_face.kps
    dist_left = np.linalg.norm(kps[2] - kps[0])
    dist_right = np.linalg.norm(kps[2] - kps[1])
    symmetry = min(dist_left, dist_right) / (max(dist_left, dist_right) + 1e-6)
    
    if symmetry < 0.65:
        raise HTTPException(status_code=400, detail=f"Face not frontal enough ({symmetry:.2f}).")
        
    raw_emb = best_face.embedding
    emb = get_face_embedding(raw_emb)
    
    if p.student_id in global_face_db and len(global_face_db[p.student_id]["embeddings"]) > 0:
        existing_embs = global_face_db[p.student_id]["embeddings"]
        sims_to_self = [cosine_similarity(emb.tolist(), e) for e in existing_embs]
        if max(sims_to_self) < 0.40:
            return {"status": "error", "message": f"🚫 IDENTITY MISMATCH: This face does not match existing record."}
    
    count = 0
    try:
        conn = get_pg_db()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM "Att_FaceEmbeddings" WHERE "StudentID" = %s', (p.student_id,))
        count = cur.fetchone()[0]
        
        if count < 15:
            cur.execute('''INSERT INTO "Att_FaceEmbeddings" ("StudentID", "Embedding", "QualityScore", "Symmetry", "SourceClassNbr")
                           VALUES (%s, %s, %s, %s, %s)''', 
                        (p.student_id, emb.tolist(), float(best_face.det_score), float(symmetry), p.class_nbr))
            conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
    if p.student_id not in global_face_db:
        global_face_db[p.student_id] = {"name": p.student_name, "embeddings": []}
    global_face_db[p.student_id]["embeddings"].append(emb.tolist())
    
    global live_tracker_memory
    live_tracker_memory.clear()
    
    return {"status": "success", "message": f"✅ {p.student_name} enrolled successfully.", "quality": "excellent", "total_embeddings": count + 1}



class UnassignPayload(BaseModel):
    student_id: str

@app.post("/api/unassign-student")
def unassign_student(p: UnassignPayload):
    # Remove from PostgreSQL
    try:
        conn = get_pg_db()
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
    conn = get_ms_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT s.StudentID, s.StudentName, c.CourseName, c.StartTime
        FROM Att_Student s
        JOIN Att_Class_List cl ON s.StudentID = cl.StudentID
        JOIN Att_Course_Class c ON cl.ClassNbr = c.ClassNbr
        WHERE cl.ClassNbr = ?
    ''', (str(payload.class_nbr),))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    report_data = []
    for row in rows:
        status = "Present" if payload.attendance_records.get(str(row[0])) == "present" else "Absent"
        report_data.append({"Student ID": row[0], "Student Name": row[1], "Course Name": row[2], "Start Time": row[3].strftime("%I:%M %p") if row[3] else "", "Attendance Status": status})

    report_df = pd.DataFrame(report_data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        report_df.to_excel(writer, index=False, sheet_name='Attendance Report')
    output.seek(0)
    return StreamingResponse(output, headers={'Content-Disposition': f'attachment; filename="Attendance_Class_{payload.class_nbr}.xlsx"'}, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

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
frontend_dist = os.path.join(BASE_DIR, "frontend", "dist")

if os.path.exists(frontend_dist):
    # Serve static assets first (js, css, images)
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    # Serve index.html for the root
    @app.get("/")
    def serve_index():
        return FileResponse(os.path.join(frontend_dist, "index.html"))

    # Catch-all route for React Router (Must be at the very bottom!)
    @app.get("/{path:path}")
    def serve_spa(path: str):
        if path.startswith("api/") or path.startswith("ws/"):
            raise HTTPException(status_code=404, detail="API route not found")
        return FileResponse(os.path.join(frontend_dist, "index.html"))
else:
    print("⚠️ WARNING: frontend/dist not found. Run 'npm run build' in the frontend folder.")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)