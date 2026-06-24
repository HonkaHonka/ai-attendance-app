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
import pickle
import torch
from PIL import Image
import io
import asyncio
from ultralytics import YOLO
from facenet_pytorch import InceptionResnetV1
import cv2
from typing import Dict
import time
import shutil
from typing import Optional
from fastapi import UploadFile, File
from fastapi import Request
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
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

MEMORY_FILE = os.path.join(DATA_DIR, "face_memory.pkl")
BEST_MEMORY_FILE = os.path.join(DATA_DIR, "face_memory_best.pkl")

def load_best_db():
    if not os.path.exists(BEST_MEMORY_FILE): return {}
    with open(BEST_MEMORY_FILE, "rb") as f: return pickle.load(f)

def save_best_db(db):
    for sid, data in db.items():
        if len(data["embeddings"]) > 8:
            data["embeddings"] = data["embeddings"][:8]
            data["quality"] = data["quality"][:8]
    
    with open(BEST_MEMORY_FILE, "wb") as f:
        pickle.dump(db, f)
    print(f"🏆 BEST DB saved: {len(db)} students, {sum(len(v['embeddings']) for v in db.values())} total embeddings")

def is_high_quality_embedding(prob, symmetry):
    return prob > 0.85 and symmetry > 0.80

def add_to_best_db(student_id, student_name, emb, prob, symmetry):
    if student_id not in global_best_db:
        global_best_db[student_id] = {"name": student_name, "embeddings": [], "quality": []}
    
    best_existing = global_best_db[student_id]["embeddings"]
    
    # Redundancy check for best DB (stricter: only distinct angles)
    if len(best_existing) > 0:
        sims = [cosine_similarity(emb.tolist(), e) for e in best_existing]
        if max(sims) > 0.90:
            return False  # Already have this angle in best DB
    
    best_existing.append(emb.tolist())
    global_best_db[student_id]["quality"].append({"prob": prob, "symmetry": symmetry})
    
    # If over 8, evict the lowest quality embedding
    if len(best_existing) > 8:
        qualities = global_best_db[student_id]["quality"]
        scores = [q["prob"] * q["symmetry"] for q in qualities]
        min_idx = scores.index(min(scores))
        best_existing.pop(min_idx)
        qualities.pop(min_idx)
    
    save_best_db(global_best_db)
    return True

global_best_db = load_best_db()

def load_face_db():
    if not os.path.exists(MEMORY_FILE):
        print("📂 No existing DB found. Starting fresh.")
        return {}
    
    try:
        with open(MEMORY_FILE, "rb") as f:
            db = pickle.load(f)
        
        total_embs = sum(len(v["embeddings"]) for v in db.values())
        print(f"📂 DB LOADED: {len(db)} students, {total_embs} total embeddings from {MEMORY_FILE}")
        
        # If DB is empty but file exists, warn
        if len(db) == 0:
            print("⚠️ Warning: DB file exists but contains zero students.")
        
        return db
        
    except Exception as e:
        print(f"💥 DB LOAD FAILED: {e}")
        # Try backup
        if os.path.exists(MEMORY_FILE + ".backup"):
            print("🔄 Attempting backup restore...")
            try:
                with open(MEMORY_FILE + ".backup", "rb") as f:
                    db = pickle.load(f)
                print(f"📂 BACKUP RESTORED: {len(db)} students")
                return db
            except Exception as be:
                print(f"💥 Backup also failed: {be}")
        return {}

def save_face_db(db):
    # 🛡️ DEFENSIVE CAP: truncate any race-condition overflow
    for sid, data in db.items():
        if len(data["embeddings"]) > 8:
            data["embeddings"] = data["embeddings"][:8]
    
    # 🛡️ ATOMIC SAVE: write to temp file first, then rename
    # This prevents half-written files if Ctrl+C happens during save
    temp_file = MEMORY_FILE + ".tmp"
    with open(temp_file, "wb") as f:
        pickle.dump(db, f)
    
    # Backup existing good file
    if os.path.exists(MEMORY_FILE):
        shutil.copy2(MEMORY_FILE, MEMORY_FILE + ".backup")
    
    # Atomic rename: OS guarantees this is either complete or not happening
    os.replace(temp_file, MEMORY_FILE)
    
    print(f"💾 DB saved: {len(db)} students, {sum(len(v['embeddings']) for v in db.values())} total embeddings")

global_face_db = load_face_db()

def cosine_similarity(a, b):
    a, b = np.array(a).flatten(), np.array(b).flatten()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
def validate_db_integrity(db):
    """Check for physical corruption: NaN, wrong shape, missing keys."""
    errors = []
    for sid, data in db.items():
        if not isinstance(data, dict):
            errors.append(f"{sid}: not a dict")
            continue
        if "embeddings" not in data or "name" not in data:
            errors.append(f"{sid}: missing required keys")
            continue
        for i, emb in enumerate(data["embeddings"]):
            arr = np.array(emb)
            if arr.shape != (512,):
                errors.append(f"{sid}: emb[{i}] shape {arr.shape} != (512,)")
            if np.isnan(arr).any() or np.isinf(arr).any():
                errors.append(f"{sid}: emb[{i}] contains NaN/Inf")
    return errors
df = pd.DataFrame()

# =========================================================
# EXCEL UPLOAD ENDPOINT
# =========================================================


@app.post("/api/upload-roster")
async def upload_roster(file: UploadFile = File(...)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(400, "Only Excel files (.xlsx, .xls) are accepted")
    
    # If file exists and is locked, use a numbered suffix
    base_name = file.filename
    file_path = os.path.join(DATA_DIR, base_name)
    
    # If locked, try filename_1.xlsx, filename_2.xlsx, etc.
    counter = 1
    original_path = file_path
    while os.path.exists(file_path):
        try:
            # Test if we can open for writing
            with open(file_path, 'a+b') as test:
                pass
            break  # File exists but we can write to it
        except PermissionError:
            # File is locked, try new name
            name, ext = os.path.splitext(base_name)
            file_path = os.path.join(DATA_DIR, f"{name}_{counter}{ext}")
            counter += 1
    
    with open(file_path, "wb") as f:
        f.write(await file.read())
    
    try:
        new_df = pd.read_excel(file_path)
        new_df.columns = new_df.columns.str.strip()
        new_df = new_df.fillna("")
        
        required = ["Faculty Email", "Faculty Name", "Class Nbr", "Student ID", "Student Name"]
        missing = [c for c in required if c not in new_df.columns]
        if missing:
            os.remove(file_path)
            raise HTTPException(400, f"Missing required columns: {', '.join(missing)}")
        
        # Auto-select as active roster
        global df
        df = new_df
        
        return {
            "status": "success",
            "message": f"Roster uploaded & selected: {len(df)} rows, {df['Class Nbr'].nunique()} classes",
            "filename": os.path.basename(file_path),
            "faculties": df["Faculty Name"].unique().tolist()
        }
        
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(400, f"Failed to parse Excel: {str(e)}")

# =========================================================
# DYNAMIC ROSTER MANAGEMENT
# =========================================================

@app.get("/api/has-roster")
def has_roster():
    """Check if a roster is currently loaded."""
    return {
        "has_roster": len(df) > 0,
        "rows": len(df),
        "classes": df["Class Nbr"].nunique() if len(df) > 0 else 0
    }

@app.get("/api/list-rosters")
def list_rosters():
    """List all available Excel files in the data directory."""
    files = []
    for f in os.listdir(DATA_DIR):
        if f.endswith(('.xlsx', '.xls')):
            files.append({
                "filename": f,
                "size_kb": round(os.path.getsize(os.path.join(DATA_DIR, f)) / 1024, 1),
                "modified": time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(os.path.join(DATA_DIR, f))))
            })
    return {"files": files}

@app.post("/api/select-roster")
async def select_roster(payload: dict):
    """Select which Excel file to use as the active roster."""
    global df
    
    filename = payload.get("filename")
    if not filename:
        raise HTTPException(400, "Filename required")
    
    file_path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(404, f"File not found: {filename}")
    
    try:
        new_df = pd.read_excel(file_path)
        new_df.columns = new_df.columns.str.strip()
        new_df = new_df.fillna("")
        
        required = ["Faculty Email", "Faculty Name", "Class Nbr", "Student ID", "Student Name"]
        missing = [c for c in required if c not in new_df.columns]
        if missing:
            raise HTTPException(400, f"Missing required columns: {', '.join(missing)}")
        
        # Set as active
        df = new_df
        
        return {
            "status": "success",
            "message": f"Roster selected: {filename}",
            "rows": len(df),
            "faculties": df["Faculty Name"].unique().tolist(),
            "classes": df["Class Nbr"].nunique()
        }
        
    except Exception as e:
        raise HTTPException(400, f"Failed to load roster: {str(e)}")

@app.post("/api/clear-roster")
def clear_roster():
    """Clear the current roster (logout / reset)."""
    global df
    df = pd.DataFrame()
    return {"status": "success", "message": "Roster cleared"}


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
def generate_qr(frontend_url: str = "http://127.0.0.1:5173"): # <--- Added parameter
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
    
    qr = qrcode.QRCode(version=3, box_size=12, border=2)
    
    # URL that opens when teacher scans QR (points to React app on network)
    auth_url = f"{frontend_url}/?token={token}" # <--- Updated URL
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
async def request_email_verification(payload: dict, request: Request): # <--- Added request
    if df.empty:
        raise HTTPException(400, "No roster loaded. Please upload a roster first.")
    
    token = payload.get("token")
    email = payload.get("email")
    
    if not token or not email:
        raise HTTPException(400, "Token and email required")
    
    session = qr_sessions.get(token)
    if not session or session["status"] != "pending":
        raise HTTPException(400, "Invalid or expired QR session")
    
    user = df[df["Faculty Email"].str.lower() == email.lower()]
    if user.empty:
        raise HTTPException(404, "Faculty email not found in roster")
    
    verify_token = secrets.token_urlsafe(32)
    session["status"] = "email_entered"
    session["email"] = email
    session["faculty_name"] = user.iloc[0]["Faculty Name"]
    session["verify_token"] = verify_token
    
    # Get the actual IP address used to reach the backend
    host_url = str(request.base_url).rstrip("/")
    
    # Build verification URL (teacher clicks this in email)
    verify_url = f"{host_url}/api/confirm-verification?vt={verify_token}&t={token}"
    
    if EMAIL_ENABLED:
        success = send_verification_email(
            to_email=email,
            teacher_name=session["faculty_name"],
            verify_url=verify_url
        )
        if not success:
            raise HTTPException(500, "Failed to send verification email")
    else:
        print(f"\n{'='*60}")
        print(f"📧 DEV MODE: Verification URL for {email}:")
        print(f"   {verify_url}")
        print(f"{'='*60}\n")
    
    return {
        "status": "success",
        "message": f"Verification email sent to {email}"
    }

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

@app.get("/api/db-health")
def db_health():
    """Instant snapshot of database quality. Works with 1 student or 100."""
    report = []
    for sid, data in global_face_db.items():
        embs = data["embeddings"]
        
        if len(embs) > 1:
            sims = []
            for i in range(len(embs)):
                for j in range(i+1, len(embs)):
                    sims.append(cosine_similarity(embs[i], embs[j]))
            avg_sim = float(np.mean(sims))
            min_sim = float(np.min(sims))
        else:
            avg_sim, min_sim = 1.0, 1.0
        
        flag = "OK"
        if len(embs) > 20:
            flag = "TOO_MANY_EMBS"
        elif len(embs) == 0:
            flag = "EMPTY"
        elif min_sim < 0.75:
            flag = "HIGH_VARIANCE"
        
        report.append({
            "student_id": sid,
            "name": data["name"],
            "embedding_count": len(embs),
            "avg_self_similarity": round(avg_sim, 3),
            "min_self_similarity": round(min_sim, 3),
            "flag": flag
        })
    
    suspicious = [r for r in report if r["flag"] != "OK"]
    return {
        "total_students": len(global_face_db),
        "suspicious_count": len(suspicious),
        "suspicious": suspicious,
        "students": report
    }

@app.get("/api/health")
def health():
    """Quick system health check for IT monitoring."""
    errors = validate_db_integrity(global_face_db)
    return {
        "status": "ok" if len(errors) == 0 else "degraded",
        "camera": "connected",
        "db_students": len(global_face_db),
        "db_corrupted": len(errors) > 0,
        "db_errors": errors[:5],
        "db_file_size_kb": round(os.path.getsize(MEMORY_FILE) / 1024, 1) if os.path.exists(MEMORY_FILE) else 0,
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
    
    # --- PROFILING STATE ---
    timers = {}
    facenet_runs = 0
    yunet_detect_runs = 0
    yunet_extract_runs = 0
    db_compare_count = 0  # how many embedding-to-embedding comparisons
    process = psutil.Process(os.getpid())
    mem_before = process.memory_info().rss / 1024 / 1024  # MB
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

    frame_bgr = np.array(img)[:, :, ::-1]
    
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
    save_required = False
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
                
                # 🛡️ If no face seen in 3 seconds, degrade to "no_face"
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
                boxes_raw   = f_boxes[0]
                probs_raw   = f_probs[0]
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
                            # Count comparisons: sum of all embeddings in DB
                            db_compare_count += sum(len(v["embeddings"]) for v in global_face_db.values())
                            timers['db_search'] = timers.get('db_search', 0) + (time.perf_counter() - t_db) * 1000

                            if sid:
                                person["student_id"] = sid
                                person["name"] = name
                                person["status"] = "known"
                                person["last_recognized"] = current_time
                                
                                existing = global_face_db[sid]["embeddings"]
                                
                                if (0.72 < score < 0.94 and 
                                    probs_arr[best_idx] > 0.85 and
                                    len(existing) < 8):
                                    
                                    if len(existing) > 0:
                                        sims_to_existing = [cosine_similarity(emb.tolist(), e) for e in existing]
                                        if max(sims_to_existing) > 0.85:
                                            pass
                                        else:
                                            lm = landmarks_arr[best_idx]
                                            left_eye, right_eye, nose = lm[0], lm[1], lm[2]
                                            dist_left = np.linalg.norm(nose - left_eye)
                                            dist_right = np.linalg.norm(nose - right_eye)
                                            symmetry = min(dist_left, dist_right) / (max(dist_left, dist_right) + 1e-6)
                                            
                                            if symmetry > 0.60:
                                                existing.append(emb.tolist())
                                                save_required = True
                                                
                                                if probs_arr[best_idx] > 0.85 and symmetry > 0.80:
                                                    add_to_best_db(sid, name, emb, float(probs_arr[best_idx]), symmetry)
                                                
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

    if save_required:
        t_save = time.perf_counter()
        save_face_db(global_face_db)
        timers['db_save'] = (time.perf_counter() - t_save) * 1000

    # --- PROFILING REPORT ---
    total = (time.perf_counter() - t_start) * 1000
    mem_after = process.memory_info().rss / 1024 / 1024
    mem_delta = mem_after - mem_before
    
    # Only print if anything meaningful happened, or every 30 frames (~6 sec)
    if not hasattr(process_frame, "_frame_counter"):
        process_frame._frame_counter = 0
    process_frame._frame_counter += 1
    
    if facenet_runs > 0 or process_frame._frame_counter % 30 == 0:
        print(f"\n{'='*60}")
        print(f"📊 FRAME {process_frame._frame_counter} | People:{len(boxes) if 'boxes' in dir() else 0} | Total:{total:.1f}ms")
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
        live_tracker_memory.clear()

# =========================================================
# QUICK ASSIGN (TEACHER CLICK)
# =========================================================
class AssignPayload(BaseModel):
    student_id: str
    student_name: str
    image: str
    box: Optional[list] = None
    is_manual: bool = False
    
@app.post("/api/assign-face")
def assign_face(p: AssignPayload):
    if "," in p.image: p.image = p.image.split(",")[1]
    
    try:
        img = Image.open(io.BytesIO(base64.b64decode(p.image))).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data.")
    
    # 🎯 MANUAL ZOOM: image is already a magnified face crop
    if p.is_manual or not p.box:
        if max(img.size) > 1200:
            img.thumbnail((800, 800))
        head_crop = img
    else:
        # 🎯 YOLO TRACKING: existing body-box to head-crop logic
        x, y, w, h = map(int, p.box)
        head_h = int(h * 0.50)
        hx1 = max(0, x - 20)
        hy1 = max(0, y - 20)
        hx2 = min(img.width, x + w + 20)
        hy2 = min(img.height, y + head_h + 20)
        
        if hx2 <= hx1 or hy2 <= hy1:
            raise HTTPException(status_code=400, detail="Invalid face region.")
        
        head_crop = img.crop((hx1, hy1, hx2, hy2))
    
    # --- MTCNN detection ---
    f_boxes, f_probs, f_landmarks = mtcnn.detect(head_crop, landmarks=True)
    
    if f_boxes is None or len(f_boxes) == 0 or f_boxes[0] is None:
        raise HTTPException(status_code=400, detail="No face detected. Ask student to look at camera.")
    
    boxes_raw = f_boxes[0]
    probs_raw = f_probs[0]
    landmarks_raw = f_landmarks[0]
    
    boxes_arr = np.array(boxes_raw)
    if boxes_arr.ndim == 1: boxes_arr = boxes_arr.reshape(1, 4)
    probs_arr = np.array(probs_raw)
    if probs_arr.ndim == 0: probs_arr = probs_arr.reshape(1)
    landmarks_arr = np.array(landmarks_raw)
    if landmarks_arr.ndim == 2: landmarks_arr = landmarks_arr.reshape(1, 5, 2)
    
        # 🛡️ MANUAL ZOOM: Reject if multiple faces in crop
    if p.is_manual and len(boxes_arr) > 1:
        high_conf_count = sum(1 for prob in probs_arr if prob > 0.90)
        if high_conf_count > 1:
            raise HTTPException(
                status_code=400, 
                detail=f"Multiple faces detected in zoom ({high_conf_count} found). Please zoom closer on one student only."
            )
    
    best_idx = int(np.argmax(probs_arr))
    
    # 🚨 QUALITY GATES
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
    
    # 🎯 THIS LINE MUST EXIST AND MUST COME BEFORE emb IS COMPUTED
    face_tensor = face_tensors[0]
    with torch.no_grad():
        emb = face_net(face_tensor.unsqueeze(0).to(device)).cpu().numpy()[0]
    
    # 🛡️ IDENTITY CONSISTENCY: If student already enrolled, verify face matches them
    if p.student_id in global_face_db and len(global_face_db[p.student_id]["embeddings"]) > 0:
        existing_embs = global_face_db[p.student_id]["embeddings"]
        sims_to_self = [cosine_similarity(emb.tolist(), e) for e in existing_embs]
        max_self_sim = max(sims_to_self)
        if max_self_sim < 0.55:
            return {
                "status": "error",
                "message": f"🚫 IDENTITY MISMATCH: This face does not match existing biometric record for {global_face_db[p.student_id]['name']}. You may have selected the wrong student.",
                "max_similarity_to_record": round(max_self_sim, 3)
            }
    
    # 🛡️ DUPLICATE FACE CHECK: Does this face already belong to someone else?
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
    
    # Initialize if new student
    if p.student_id not in global_face_db:
        global_face_db[p.student_id] = {"name": p.student_name, "embeddings": []}
    
    existing = global_face_db[p.student_id]["embeddings"]
    
        # 🎯 HARD CAP: Never exceed 8 embeddings per student
    if len(existing) >= 8:
        return {
            "status": "success",
            "message": f"{p.student_name} already has maximum biometric data (8 angles). No new data saved.",
            "quality": "maxed",
            "symmetry": round(symmetry, 3),
            "confidence": round(float(probs_arr[best_idx]), 3),
            "total_embeddings": len(existing)
        }

    # 🎯 ENROLLMENT REDUNDANCY: Don't save near-duplicates for same student
    if len(existing) > 0:
        sims = [cosine_similarity(emb.tolist(), e) for e in existing]
        if max(sims) > 0.90:
            return {
                "status": "success",
                "message": f"{p.student_name} already enrolled with similar angle. No new data saved.",
                "quality": "redundant",
                "symmetry": round(symmetry, 3),
                "confidence": round(float(probs_arr[best_idx]), 3)
            }
    
        # Save the high-quality seed
    existing.append(emb.tolist())
    save_face_db(global_face_db)
    
    # 🏆 Save to BEST DB if this is truly excellent quality
    if is_high_quality_embedding(float(probs_arr[best_idx]), symmetry):
        add_to_best_db(p.student_id, p.student_name, emb, float(probs_arr[best_idx]), symmetry)
    
    # Clear tracker so next frame recognizes immediately
    global live_tracker_memory
    live_tracker_memory.clear()
    
    return {
        "status": "success",
        "message": f"✅ {p.student_name} enrolled successfully.",
        "quality": "excellent",
        "symmetry": round(symmetry, 3),
        "confidence": round(float(probs_arr[best_idx]), 3),
        "total_embeddings": len(existing)
    }



class UnassignPayload(BaseModel):
    student_id: str

@app.post("/api/unassign-student")
def unassign_student(p: UnassignPayload):
    if p.student_id in global_face_db:
        del global_face_db[p.student_id]
        save_face_db(global_face_db)
        print(f"🗑️ Removed student {p.student_id} from face DB")
    

    if p.student_id in global_best_db:
        del global_best_db[p.student_id]
        save_best_db(global_best_db)
        print(f"🗑️ Removed student {p.student_id} from best DB")

    # Clear tracker so the face immediately becomes unknown again
    global live_tracker_memory
    live_tracker_memory.clear()
    
    return {
        "status": "success", 
        "message": f"Student unassigned. Biometric data removed.",
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

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)