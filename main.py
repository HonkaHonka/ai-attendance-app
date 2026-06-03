from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from starlette.responses import StreamingResponse
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
from typing import Dict
import time
import shutil
from typing import Optional

# =========================================================
# APP SETUP
# =========================================================
app = FastAPI(title="AI Live Attendance Backend")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

device = torch.device("cpu")
print(f"✅ RUNNING ON {device}")

print("🔹 Loading YOLOv8n (PERSON TRACKING)...")
yolo_person = YOLO("yolov8n_openvino_model/", task="detect") 
print(f"🔥 YOLO device: {yolo_person.device if hasattr(yolo_person, 'device') else 'unknown'}")

print("🔹 Loading Face Quality Gate (MTCNN)...")
mtcnn = MTCNN(keep_all=False, device=device)

print("🔹 Loading Face Embedding Model...")
face_net = InceptionResnetV1(pretrained="vggface2").eval().to(device)

DATA_FILE = "data/KHC_REGISTERED_STUDENTS_31560.xlsx"
MEMORY_FILE = "data/face_memory.pkl"
BEST_MEMORY_FILE = "data/face_memory_best.pkl"
os.makedirs("data", exist_ok=True)

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
    return prob > 0.99 and symmetry > 0.80

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
        return {}
    try:
        with open(MEMORY_FILE, "rb") as f:
            db = pickle.load(f)
        errors = validate_db_integrity(db)
        if errors:
            print(f"⚠️ DB CORRUPTION DETECTED: {len(errors)} errors")
            for e in errors[:5]: 
                print(f"  - {e}")
            # Auto-restore from backup
            if os.path.exists(MEMORY_FILE + ".backup"):
                print("🔄 Restoring from backup...")
                with open(MEMORY_FILE + ".backup", "rb") as f:
                    db = pickle.load(f)
                # Validate backup too
                backup_errors = validate_db_integrity(db)
                if backup_errors:
                    print(f"💥 Backup also corrupted ({len(backup_errors)} errors). Starting fresh.")
                    return {}
        return db
    except Exception as e:
        print(f"💥 DB LOAD FAILED: {e}")
        return {}

def save_face_db(db):
    # 🛡️ DEFENSIVE CAP: truncate any race-condition overflow
    for sid, data in db.items():
        if len(data["embeddings"]) > 8:
            data["embeddings"] = data["embeddings"][:8]
    
    if os.path.exists(MEMORY_FILE):
        shutil.copy2(MEMORY_FILE, MEMORY_FILE + ".backup")
    with open(MEMORY_FILE, "wb") as f:
        pickle.dump(db, f)
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
    
    if not image_b64:
        return []
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]
    if not image_b64:
        return []

    try:
        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return []

    frame_bgr = np.array(img)[:, :, ::-1]
    
    results = yolo_person.track(
        frame_bgr,
        conf=0.60,      # ← Raised from 0.45
        iou=0.50,       # ← Raised from 0.40 (less overlapping boxes)
        classes=[0],
        tracker="botsort.yaml",
        persist=True,
        verbose=False
    )
    
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
                "last_seen": current_time
            })
            person["last_seen"] = current_time

                        # 🎯 PERFORMANCE: Skip expensive face extraction if recently recognized
            if person.get("status") == "known" and (current_time - person.get("last_recognized", 0)) < 3.0:
                # Just update position, reuse cached identity
                current_frame_tracks[track_id] = person
                faces_out.append({
                    "box": [x1, y1, x2 - x1, y2 - y1],
                    "track_id": track_id,
                    "student_id": person["student_id"],
                    "name": person["name"],
                    "status": "known"
                })
                continue

            head_h = int((y2 - y1) * 0.50)
            hx1 = max(0, x1 - 20)
            hy1 = max(0, y1 - 20)
            hx2 = min(img.width, x2 + 20)
            hy2 = min(img.height, y1 + head_h + 20)
            
            if hx2 <= hx1 or hy2 <= hy1:
                continue
                
            head_crop = img.crop((hx1, hy1, hx2, hy2))

            # --- MTCNN detect + landmarks ---
            f_boxes, f_probs, f_landmarks = mtcnn.detect(head_crop, landmarks=True)
            
            face_found = False
            face_abs_box = None
            
            if f_boxes is not None and len(f_boxes) > 0 and f_boxes[0] is not None:
                boxes_raw   = f_boxes[0]
                probs_raw   = f_probs[0]
                landmarks_raw = f_landmarks[0]
                
                # Robust shape normalization
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
                    
                    if probs_arr[best_idx] > 0.90:
                        face_found = True
                        
                        # 🎯 ABSOLUTE FACE BOX for frontend zoom centering
                        fb = boxes_arr[best_idx]
                        face_abs_box = [
                            int(hx1 + fb[0]),
                            int(hy1 + fb[1]),
                            int(fb[2] - fb[0]),
                            int(fb[3] - fb[1])
                        ]
                        person["frames_no_face"] = 0

                        best_box = boxes_arr[best_idx:best_idx+1]
                        
                        extracted = mtcnn.extract(head_crop, best_box, save_path=None)
                        
                        # 🛡️ FIX: Handle Tensor/list/None return from extract()
                        if extracted is None:
                            face_tensors = []
                        elif isinstance(extracted, torch.Tensor):
                            face_tensors = [extracted]
                        else:
                            face_tensors = extracted
                        
                        if len(face_tensors) > 0:
                            face_tensor = face_tensors[0]
                            
                            with torch.no_grad():
                                emb = face_net(face_tensor.unsqueeze(0).to(device)).cpu().numpy()[0]
                            
                            sid, name, score = recognize_face(emb)

                            if sid:
                                person["student_id"] = sid
                                person["name"] = name
                                person["status"] = "known"
                                person["last_recognized"] = current_time
                                existing = global_face_db[sid]["embeddings"]
                                
                                                                # 🧠 SMART ACTIVE LEARNING: Enrich only if under cap and view is new
                                if (0.72 < score < 0.94 and 
                                    probs_arr[best_idx] > 0.97 and   # ← Lowered from 0.99 to capture more angles
                                    len(existing) < 8):
                                    
                                    # Check if this angle/view is already represented
                                    if len(existing) > 0:
                                        sims_to_existing = [cosine_similarity(emb.tolist(), e) for e in existing]
                                        if max(sims_to_existing) > 0.85:  # ← Lowered from 0.88 to allow more diversity
                                            # Already have this angle, skip to prevent bloat
                                            pass
                                        else:
                                            # New angle detected, check symmetry
                                            lm = landmarks_arr[best_idx]
                                            left_eye, right_eye, nose = lm[0], lm[1], lm[2]
                                            dist_left = np.linalg.norm(nose - left_eye)
                                            dist_right = np.linalg.norm(nose - right_eye)
                                            symmetry = min(dist_left, dist_right) / (max(dist_left, dist_right) + 1e-6)
                                            
                                            if symmetry > 0.60:  # 🎯 Accept side profiles for classroom reality
                                                existing.append(emb.tolist())
                                                save_required = True
                                                
                                                # 🏆 BEST DB: Only save if truly excellent quality
                                                if probs_arr[best_idx] > 0.99 and symmetry > 0.80:
                                                    add_to_best_db(sid, name, emb, float(probs_arr[best_idx]), symmetry)
                                                
                                                session_stats["embeddings_added"][sid] = session_stats["embeddings_added"].get(sid, 0) + 1
                                                session_stats["avg_symmetry"].append(symmetry)
                                                if session_stats["start_time"] is None:
                                                    session_stats["start_time"] = time.time()
                                                
                                                print(f"🧠 ACTIVE LEARN: New angle for {name} (sym: {symmetry:.2f}, count: {len(existing)})")
                                            else:
                                                session_stats["rejected_by_symmetry"] += 1
                                    else:
                                        # Should not happen (student has at least enrollment embedding), but safety
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

            current_frame_tracks[track_id] = person

            if person["status"] != "no_face":
                face_out = {
                    "box": [x1, y1, x2 - x1, y2 - y1],
                    "track_id": track_id,  # 🎯 Send track_id so frontend can follow you
                    "student_id": person["student_id"],
                    "name": person["name"],
                    "status": person["status"]
                }
                if face_abs_box:
                    face_out["face_box"] = face_abs_box
                faces_out.append(face_out)

    # --- TTL Tracker Memory ---
    for tid, tdata in current_frame_tracks.items():
        live_tracker_memory[tid] = tdata
    
    live_tracker_memory = {
        tid: tdata for tid, tdata in live_tracker_memory.items()
        if (current_time - tdata.get("last_seen", 0)) < 10.0
    }

    if save_required:
        save_face_db(global_face_db)

    # --- MEMORY LEAK MONITOR ---
    if not hasattr(process_frame, "_last_mem_report"):
        process_frame._last_mem_report = 0
    if current_time - process_frame._last_mem_report > 10:
        print(f"🧠 MEM-TRACK: {len(live_tracker_memory)} active tracks | DB: {len(global_face_db)} students")
        process_frame._last_mem_report = current_time

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
    if probs_arr[best_idx] < 0.95:
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