import React, { useState, useRef, useEffect } from 'react';
import Webcam from "react-webcam";
import './App.css';

const API_BASE = "http://127.0.0.1:8000/api";
const WS_BASE = "ws://127.0.0.1:8000/ws";

// Camera constraints
const SURVEILLANCE_CONSTRAINTS = {
  facingMode: "user",
  width: { ideal: 1920 },
  height: { ideal: 1080 }
};

const ENROLL_CONSTRAINTS = {
  facingMode: "user",
  width: 1280, height: 720
};

const AI_W = 1280;
const AI_H = 720;

function App() {
  // ==========================================
  // STATE
  // ==========================================
  const [view, setView] = useState('login'); 
  const [email, setEmail] = useState('');
  const [facultyName, setFacultyName] = useState('');
  const [classes, setClasses] = useState([]);
  const [students, setStudents] = useState([]);
  const [selectedClass, setSelectedClass] = useState('');
  const [error, setError] = useState('');

  // REMOVED: isModalOpen, enrollStep, capturedImages, isCapturing — live feed only
  
  const [isVerifyModalOpen, setIsVerifyModalOpen] = useState(false);
  const [verifyResult, setVerifyResult] = useState('');
  const [verifyingStudent, setVerifyingStudent] = useState(null); 
  const [attendanceRecords, setAttendanceRecords] = useState({}); 

  const [isSurveillanceActive, setIsSurveillanceActive] = useState(false);
  const [detectedFaces, setDetectedFaces] = useState([]);
  const [quickEnrollData, setQuickEnrollData] = useState(null); 
  const [liveZoom, setLiveZoom] = useState(null); 
  const [inspectMode, setInspectMode] = useState(null);
  const [wheelZoom, setWheelZoom] = useState(null);

  // REFS
  const webcamRef = useRef(null);
  const canvasRef = useRef(null);
  const wsRef = useRef(null);
  const surveillanceWebcamRef = useRef(null); 
  const inspectCanvasRef = useRef(null); 

  // ==========================================
  // API CALLS
  // ==========================================
  const handleLogin = async (e) => {
    e.preventDefault();
    setError('');
    try {
      const res = await fetch(`${API_BASE}/login?email=${encodeURIComponent(email)}`);
      if (!res.ok) throw new Error("Faculty email not found or Server is offline.");
      const data = await res.json();
      setFacultyName(data.name);
      fetchClasses(email);
    } catch (err) { setError(err.message); }
  };

  const fetchClasses = async (userEmail) => {
    try {
      const res = await fetch(`${API_BASE}/classes?email=${encodeURIComponent(userEmail)}`);
      setClasses(await res.json());
      setView('classes'); 
      stopSurveillance(); 
    } catch (err) { alert("Error loading classes"); }
  };

  const fetchStudents = async (classNbr) => {
    try {
      const res = await fetch(`${API_BASE}/students?email=${encodeURIComponent(email)}&class_nbr=${classNbr}`);
      setStudents(await res.json());
      setSelectedClass(classNbr);
      setAttendanceRecords({}); 
      setView('students');
    } catch (err) { alert("Error loading students"); }
  };

  const downloadAttendanceReport = async () => {
    try {
      const response = await fetch(`${API_BASE}/export-attendance`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ class_nbr: Number(selectedClass), attendance_records: attendanceRecords })
      });
      if (!response.ok) throw new Error("Failed to generate report");

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `Attendance_Class_${selectedClass}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) { alert(`Error downloading report: ${error.message}`); }
  };

  const runVerificationScan = async () => {
    setVerifyResult('Scanning...');
    const imageSrc = webcamRef.current.getScreenshot();
    try {
      const response = await fetch(`${API_BASE}/verify-face`, { 
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' }, 
        body: JSON.stringify({ image: imageSrc }) 
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail);
      
      if (result.match && result.name === verifyingStudent['Student Name']) {
        setVerifyResult(`✅ Identity Verified: ${result.name}`);
        setAttendanceRecords(prev => ({ ...prev,[verifyingStudent['Student ID']]: 'present' }));
        setTimeout(() => { setIsVerifyModalOpen(false); }, 1500);
      } else if (result.match) {
        setVerifyResult(`❌ Mismatch! That face belongs to: ${result.name}`);
        setAttendanceRecords(prev => ({ ...prev, [verifyingStudent['Student ID']]: 'failed' }));
      } else {
        setVerifyResult(`❌ Face Not Recognized in Database.`);
        setAttendanceRecords(prev => ({ ...prev, [verifyingStudent['Student ID']]: 'failed' }));
      }
    } catch (error) { setVerifyResult(`⚠️ Error: ${error.message}`); }
  };

  // ==========================================
  // WEBSOCKET & SURVEILLANCE
  // ==========================================
  const sendFrameToWebSocket = () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && surveillanceWebcamRef.current && surveillanceWebcamRef.current.video) {
      const video = surveillanceWebcamRef.current.video;
      
      if (video.videoWidth > 0) {
        const tempCanvas = document.createElement('canvas');
        tempCanvas.width = AI_W;
        tempCanvas.height = AI_H;
        const ctx = tempCanvas.getContext('2d');
        ctx.drawImage(video, 0, 0, AI_W, AI_H);
        const imageSrc = tempCanvas.toDataURL('image/jpeg', 0.6); 
        
        wsRef.current.send(JSON.stringify({ image: imageSrc }));
      } else {
        requestAnimationFrame(sendFrameToWebSocket);
      }
    }
  };

  const toggleSurveillance = () => {
    if (isSurveillanceActive) {
      stopSurveillance();
    } else {
      setIsSurveillanceActive(true);
      wsRef.current = new WebSocket(`${WS_BASE}/surveillance`);
      wsRef.current.onopen = () => { sendFrameToWebSocket(); };
      wsRef.current.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.faces) {
          setDetectedFaces(data.faces);
          const newRecords = {};
          data.faces.forEach(face => {
            if (face.status === 'known' && face.student_id) newRecords[face.student_id] = 'present';
          });
          setAttendanceRecords(prev => ({ ...prev, ...newRecords }));
        }
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          requestAnimationFrame(sendFrameToWebSocket);
        }
      };
      wsRef.current.onerror = () => { stopSurveillance(); };
    }
  };

  const stopSurveillance = () => {
    setIsSurveillanceActive(false);
    if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
    setDetectedFaces([]);
    setLiveZoom(null);
    setQuickEnrollData(null);
    setInspectMode(null);
    setWheelZoom(null);
  };

  useEffect(() => { return () => stopSurveillance(); },[]);

  // ==========================================
  // CANVAS OVERLAY DRAWING
  // ==========================================
  useEffect(() => {
    if (canvasRef.current && detectedFaces) {
      const canvas = canvasRef.current;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, AI_W, AI_H);

      detectedFaces.forEach(face => {
        const [x, y, w, h] = face.box;

        ctx.beginPath();
        ctx.lineWidth = 4;
        if (face.status === 'known') { ctx.strokeStyle = '#28a745'; ctx.fillStyle = '#28a745'; } 
        else if (face.status === 'scanning') { ctx.strokeStyle = '#ffcb05'; ctx.fillStyle = '#ffcb05'; }
        else { ctx.strokeStyle = '#dc3545'; ctx.fillStyle = '#dc3545'; }
        
        ctx.rect(x, y, w, h);
        ctx.stroke();

        ctx.font = 'bold 24px Arial';
        const text = face.name;
        const textWidth = ctx.measureText(text).width;
        ctx.fillRect(x, y - 35, textWidth + 20, 35);
        ctx.fillStyle = '#fff';
        ctx.fillText(text, x + 10, y - 8);
      });
    }
  },[detectedFaces]);

  // ==========================================
  // INSPECT CANVAS RENDERER (right-click zoom)
  // ==========================================
    useEffect(() => {
    let animId;
    
    const drawInspect = () => {
      if (!inspectMode || !surveillanceWebcamRef.current?.video || !inspectCanvasRef.current) {
        animId = requestAnimationFrame(drawInspect);
        return;
      }
      
      const video = surveillanceWebcamRef.current.video;
      const canvas = inspectCanvasRef.current;
      const ctx = canvas.getContext('2d');
      
      const vidW = video.videoWidth || 1920;
      const vidH = video.videoHeight || 1080;
      
      const liveFace = detectedFaces.find(f => f.track_id === inspectMode.trackId);
      
      let centerX = AI_W / 2;
      let centerY = AI_H / 2;
      
      if (liveFace) {
        // Use face_box center if available, else body box center
        if (liveFace.face_box) {
          const [fx, fy, fw, fh] = liveFace.face_box;
          centerX = fx + fw / 2;
          centerY = fy + fh / 2;
        } else {
          const [bx, by, bw, bh] = liveFace.box;
          centerX = bx + bw / 2;
          centerY = by + bh * 0.25; // upper body guess
        }
      }
      
      const outputW = 640;
      const outputH = 480;
      const scale = inspectMode.scale;
      
      const srcW = (outputW / scale) * (vidW / AI_W);
      const srcH = (outputH / scale) * (vidH / AI_H);
      
      const srcX = Math.max(0, Math.min(vidW - srcW, (centerX * (vidW / AI_W)) - srcW / 2));
      const srcY = Math.max(0, Math.min(vidH - srcH, (centerY * (vidH / AI_H)) - srcH / 2));
      
      canvas.width = outputW;
      canvas.height = outputH;
      ctx.drawImage(video, srcX, srcY, srcW, srcH, 0, 0, outputW, outputH);
      
      // Draw FACE-level rectangle, not body box
      if (liveFace) {
        let fx, fy, fw, fh;
        
        if (liveFace.face_box) {
          [fx, fy, fw, fh] = liveFace.face_box;
        } else {
          // Estimate face in upper body if backend didn't send face_box
          const [bx, by, bw, bh] = liveFace.box;
          fx = bx + bw * 0.2;
          fy = by;
          fw = bw * 0.6;
          fh = bh * 0.45;
        }
        
        const sx = ((fx * (vidW / AI_W)) - srcX) * (outputW / srcW);
        const sy = ((fy * (vidH / AI_H)) - srcY) * (outputH / srcH);
        const sw = fw * (vidW / AI_W) * (outputW / srcW);
        const sh = fh * (vidH / AI_H) * (outputH / srcH);
        ctx.strokeStyle = '#00ff00';
        ctx.lineWidth = 3;
        ctx.strokeRect(sx, sy, sw, sh);
      }
      
      animId = requestAnimationFrame(drawInspect);
    };
    
    drawInspect();
    return () => cancelAnimationFrame(animId);
  }, [inspectMode, detectedFaces]);

  // ==========================================
  // WHEEL ZOOM RENDERER
  // ==========================================
  useEffect(() => {
    let animId;
    
    const drawWheelZoom = () => {
      if (!wheelZoom || !surveillanceWebcamRef.current?.video) {
        animId = requestAnimationFrame(drawWheelZoom);
        return;
      }
      
      const video = surveillanceWebcamRef.current.video;
      const vidW = video.videoWidth || 1920;
      const vidH = video.videoHeight || 1080;
      
      const { centerX, centerY, scale, trackId } = wheelZoom;
      
      let liveFace = null;
      if (trackId) {
        liveFace = detectedFaces.find(f => f.track_id === trackId);
      }
      
      let currentCenterX = centerX;
      let currentCenterY = centerY;
      
      if (liveFace && liveFace.face_box) {
        const [fx, fy, fw, fh] = liveFace.face_box;
        currentCenterX = (fx + fw / 2);
        currentCenterY = (fy + fh / 2);
      } else if (liveFace) {
        const [bx, by, bw, bh] = liveFace.box;
        currentCenterX = (bx + bw / 2);
        currentCenterY = (by + bh * 0.25);
      }
      
      const zoomCanvas = document.getElementById('wheel-zoom-canvas');
      if (!zoomCanvas) {
        animId = requestAnimationFrame(drawWheelZoom);
        return;
      }
      
      const ctx = zoomCanvas.getContext('2d');
      const outputW = 640;
      const outputH = 480;
      
      const srcW = (outputW / scale) * (vidW / AI_W);
      const srcH = (outputH / scale) * (vidH / AI_H);
      
      const srcX = Math.max(0, Math.min(vidW - srcW, (currentCenterX * (vidW / AI_W)) - srcW / 2));
      const srcY = Math.max(0, Math.min(vidH - srcH, (currentCenterY * (vidH / AI_H)) - srcH / 2));
      
      zoomCanvas.width = outputW;
      zoomCanvas.height = outputH;
      ctx.drawImage(video, srcX, srcY, srcW, srcH, 0, 0, outputW, outputH);
      
      if (liveFace && liveFace.face_box) {
        const [fx, fy, fw, fh] = liveFace.face_box;
        const sx = ((fx * (vidW / AI_W)) - srcX) * (outputW / srcW);
        const sy = ((fy * (vidH / AI_H)) - srcY) * (outputH / srcH);
        const sw = fw * (vidW / AI_W) * (outputW / srcW);
        const sh = fh * (vidH / AI_H) * (outputH / srcH);
        ctx.strokeStyle = '#00ff00';
        ctx.lineWidth = 3;
        ctx.strokeRect(sx, sy, sw, sh);
      }
      
      animId = requestAnimationFrame(drawWheelZoom);
    };
    
    drawWheelZoom();
    return () => cancelAnimationFrame(animId);
  }, [wheelZoom, detectedFaces]);

  // ==========================================
  // MOUSE HANDLERS
  // ==========================================
  const handleCanvasClick = (e) => {
    if (e.button !== 0) return;

    const canvas = canvasRef.current;
    if (!canvas) return;
    
    const rect = canvas.getBoundingClientRect();
    const scaleX = AI_W / rect.width;
    const scaleY = AI_H / rect.height;
    
    const clickX = (e.clientX - rect.left) * scaleX;
    const clickY = (e.clientY - rect.top) * scaleY;

    detectedFaces.forEach(face => {
      const [x, y, w, h] = face.box;
      
      if ((face.status === 'unknown' || face.status === 'scanning') && 
          clickX >= x && clickX <= x + w && 
          clickY >= y && clickY <= y + h) {
        
        const video = surveillanceWebcamRef.current?.video;
        if (!video || video.readyState < 2) return;
        
        const tempCanvas = document.createElement('canvas');
        tempCanvas.width = AI_W;
        tempCanvas.height = AI_H;
        tempCanvas.getContext('2d').drawImage(video, 0, 0, AI_W, AI_H);
        
        const assignBox = face.box;
        
        setQuickEnrollData({ image: tempCanvas.toDataURL('image/jpeg', 0.8), box: assignBox, isManual: false });
        setLiveZoom({ origBox: assignBox, isManual: false });
      }
    });
  };

  const handleCanvasRightClick = (e) => {
    e.preventDefault();

    const canvas = canvasRef.current;
    if (!canvas) return;
    
    const rect = canvas.getBoundingClientRect();
    const clickX_AI = (e.clientX - rect.left) * (AI_W / rect.width);
    const clickY_AI = (e.clientY - rect.top) * (AI_H / rect.height);

    detectedFaces.forEach(face => {
      const [x, y, w, h] = face.box;
      
      if (clickX_AI >= x && clickX_AI <= x + w && clickY_AI >= y && clickY_AI <= y + h) {
        const boxArea = w * h;
        const canvasArea = AI_W * AI_H;
        const ratio = boxArea / canvasArea;
        
        let zoomScale;
        if (ratio < 0.015) zoomScale = 3.0;
        else if (ratio < 0.04) zoomScale = 2.0;
        else zoomScale = 1.3;
        
        setInspectMode({
          trackId: face.track_id,
          scale: zoomScale
        });
        setWheelZoom(null);
      }
    });
  };

    const handleCanvasWheel = (e) => {
    e.preventDefault();
    
    const canvas = canvasRef.current;
    if (!canvas) return;
    
    const rect = canvas.getBoundingClientRect();
    const mouseX = (e.clientX - rect.left) * (AI_W / rect.width);
    const mouseY = (e.clientY - rect.top) * (AI_H / rect.height);
    
    let targetFace = null;
    for (const face of detectedFaces) {
      const [x, y, w, h] = face.box;
      if (mouseX >= x && mouseX <= x + w && mouseY >= y && mouseY <= y + h) {
        targetFace = face;
        break;
      }
    }
    
    if (e.deltaY < 0) {
      // WHEEL UP = ZOOM IN
      if (targetFace) {
        const boxArea = targetFace.box[2] * targetFace.box[3];
        const canvasArea = AI_W * AI_H;
        const ratio = boxArea / canvasArea;
        
        let zoomScale;
        if (ratio < 0.003) zoomScale = 5.0;       // Extreme back row
        else if (ratio < 0.008) zoomScale = 4.0;    // Very far
        else if (ratio < 0.02) zoomScale = 3.0;     // Back row
        else if (ratio < 0.05) zoomScale = 2.0;     // Middle
        else zoomScale = 1.3;                        // Front row
        
        setWheelZoom({
          centerX: mouseX,
          centerY: mouseY,
          scale: zoomScale,
          trackId: targetFace.track_id,
          faceBox: targetFace.face_box || null
        });
        setInspectMode(null);
      } else {
        // Empty area zoom (YOLO missed them) — default high zoom
        setWheelZoom({
          centerX: mouseX,
          centerY: mouseY,
          scale: 3.5,  // Higher default for back-row discovery
          trackId: null,
          faceBox: null
        });
      }
    } else {
      // WHEEL DOWN = ZOOM OUT
      setWheelZoom(null);
    }
  };

  // ==========================================
  // CAPTURE & ASSIGNMENT LOGIC
  // ==========================================
  const captureManualZoom = () => {
    const zoomCanvas = document.getElementById('wheel-zoom-canvas');
    if (!zoomCanvas) return;
    
    const imageSrc = zoomCanvas.toDataURL('image/jpeg', 0.9);
    
    setQuickEnrollData({ image: imageSrc, box: null, isManual: true });
    setLiveZoom({ origBox: null, isManual: true });
    setWheelZoom(null);
  };
    const captureWheelZoomTracked = () => {
    const zoomCanvas = document.getElementById('wheel-zoom-canvas');
    if (!zoomCanvas) return;
    
    // The zoom canvas is already centered on the tracked face
    const imageSrc = zoomCanvas.toDataURL('image/jpeg', 0.9);
    
    // Treat as manual because the canvas IS the crop
    setQuickEnrollData({ image: imageSrc, box: null, isManual: true });
    setLiveZoom({ origBox: null, isManual: true });
    setWheelZoom(null);
  };
  const assignFromInspect = () => {
    if (!inspectMode) return;
    
    const liveFace = detectedFaces.find(f => f.track_id === inspectMode.trackId);
    if (!liveFace) {
      alert('⚠️ Student moved out of frame. Please wait for them to reappear.');
      return;
    }
    
    const video = surveillanceWebcamRef.current?.video;
    if (!video || video.readyState < 2) {
      alert('⚠️ Camera not ready.');
      return;
    }
    
    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = AI_W;
    tempCanvas.height = AI_H;
    const ctx = tempCanvas.getContext('2d');
    ctx.drawImage(video, 0, 0, AI_W, AI_H);
    
    const assignBox = liveFace.box;
    
    setQuickEnrollData({ 
      image: tempCanvas.toDataURL('image/jpeg', 0.8),
      box: assignBox,
      isManual: false
    });
    setLiveZoom({ origBox: assignBox, isManual: false });
    setInspectMode(null);
  };

  const assignLiveEnroll = async (studentId, studentName) => {
    try {
      const payload = {
        student_id: String(studentId),
        student_name: studentName,
        image: quickEnrollData.image,
        box: quickEnrollData.box || null,
        is_manual: quickEnrollData.isManual || false
      };

      const response = await fetch(`${API_BASE}/assign-face`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
      });
      
      let result;
      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        result = await response.json();
      } else {
        const text = await response.text();
        throw new Error(text || `Server error: ${response.status}`);
      }
      
if (!response.ok) {
  const errMsg = typeof result.detail === 'string' 
    ? result.detail 
    : (Array.isArray(result.detail) 
        ? result.detail.map(d => d.msg || JSON.stringify(d)).join('; ') 
        : JSON.stringify(result.detail || result));
  throw new Error(errMsg || result.message || 'Assignment failed');
}
      
      if (result.status === "error") {
        alert(`❌ ${result.message}`);
        return;
      }
      
      if (result.quality === "redundant") {
        alert(`ℹ️ ${result.message}`);
      } else {
        alert(`✅ ${result.message}`);
      }
      
      setLiveZoom(null); 
      setQuickEnrollData(null);
      
    } catch (error) { 
      alert(`❌ Quick Enroll Error: ${error.message}`); 
    }
  };

  // ==========================================
  // RENDER
  // ==========================================
  return (
    <div>
      {/* TOP BAR */}
      <div className="top-bar">
        <div>✉ info@lu.ac.ae &nbsp;&nbsp; 📞 600 500606</div>
        <div className="top-bar-right"><span>Our Campuses</span> <span>LU Connect</span> <span>Library Portal</span></div>
      </div>
      <nav className="main-nav">
        <div className="logo">🛡️ Liwa <span>University</span></div>
        <div className="nav-links"><a>Home</a><a>Study</a><a>Admissions</a><a>Research</a><a>Student Life</a><a>About Us</a></div>
      </nav>

      {/* LOGIN */}
      {view === 'login' && (
        <div className="hero-section">
          <div className="login-card">
            <h2>Faculty Portal</h2>
            <form onSubmit={handleLogin}>
              <input type="email" placeholder="Enter email (e.g. ihab.awad@lu.ac.ae)" value={email} onChange={(e) => setEmail(e.target.value)} required />
              {error && <p style={{color: 'red', fontSize: '14px', fontWeight: 'bold'}}>{error}</p>}
              <button type="submit" className="btn-primary">Sign In to Portal</button>
            </form>
          </div>
        </div>
      )}

      {/* CLASSES */}
      {view === 'classes' && (
        <div className="app-container">
          <div className="content-box">
            <h2 className="section-title">Faculty Dashboard</h2>
            <h3 style={{color: '#555', marginTop: 0}}>Welcome, {facultyName}</h3>
            <div style={{overflowX: 'auto'}}>
              <table>
                <thead>
                  <tr>
                    <th>Class Nbr</th><th>Semester</th><th>Course Code</th>
                    <th>Course Name</th><th>Start Time</th><th>Room ID</th>
                    <th style={{textAlign: 'center', minWidth: '250px'}}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {classes.map((cls, idx) => (
                    <tr key={idx} style={{ cursor: 'default' }}>
                      <td>{cls['Class Nbr']}</td><td>{cls['Semester']}</td><td>{cls['Course Code']}</td>
                      <td>{cls['Course Name']}</td><td>{cls['Start Time']}</td><td>{cls['Room ID']}</td>
                      <td style={{textAlign: 'center'}}>
                        <button className="btn-enroll-small" style={{background: '#2f3254', color: 'white'}} onClick={() => fetchStudents(cls['Class Nbr'])}>📋 Check List</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* STUDENTS */}
      {view === 'students' && (
        <div className="app-container">
          <div className="content-box">
            <button className="back-btn" onClick={() => setView('classes')}>← Back to Classes</button>
            
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
              <h2 className="section-title" style={{ display: 'inline-block', margin: 0 }}>Class Roster: {selectedClass}</h2>
              <button style={{ background: '#28a745', color: 'white', padding: '10px 20px', borderRadius: '5px', border: 'none', cursor: 'pointer', fontWeight: 'bold' }} onClick={downloadAttendanceReport}>
                📥 Download Excel Report
              </button>
            </div>
            
            <div style={{background: '#f8f9fa', padding: '30px', borderRadius: '8px', border: '2px solid #e9ecef', marginBottom: '30px', textAlign: 'center'}}>
              <h3 style={{marginTop: 0, color: 'var(--primary-dark)', fontSize: '24px'}}>🎥 Live Classroom Surveillance</h3>
              <p style={{color: '#666', fontSize: '16px', marginBottom: '20px'}}>YOLOv8 + PyTorch crowd tracking. <b>Left-click Red boxes to assign</b> | <b>Right-click to inspect/zoom</b> | <b>Mouse wheel to dynamic zoom</b></p>
              <button style={{background: '#2f3254', color: 'white', padding: '15px 40px', borderRadius: '30px', border: 'none', cursor: 'pointer', fontWeight: 'bold', fontSize: '18px', boxShadow: '0 4px 10px rgba(0,0,0,0.2)'}} onClick={toggleSurveillance}>
                ▶ Launch Full-Screen Tracker
              </button>
            </div>

            <div style={{maxWidth: '800px', margin: '0 auto'}}>
              <table>
                <thead>
                  <tr><th>Student ID</th><th>Student Name</th><th style={{textAlign: 'center'}}>Attendance Status</th></tr>
                </thead>
                <tbody>
                  {students.map((student, idx) => {
                    const status = attendanceRecords[student['Student ID']];
                    return (
                      <tr key={idx}>
                        <td>{student['Student ID']}</td><td>{student['Student Name']}</td>
                        <td style={{textAlign: 'center'}}>
                          {status === 'present' ? (
                            <span style={{ color: 'white', background: '#28a745', padding: '6px 16px', borderRadius: '4px', fontWeight: 'bold', display: 'inline-block', width: '120px' }}>✅ Present</span>
                          ) : (
                            <button 
                              style={{ padding: '6px 12px', backgroundColor: status === 'failed' ? '#dc3545' : '#e9ecef', color: status === 'failed' ? 'white' : '#333', border: '1px solid #ccc', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold', width: '150px' }}
                              onClick={() => { setVerifyingStudent(student); setVerifyResult(''); setIsVerifyModalOpen(true); }}
                            >
                              {status === 'failed' ? '❌ Retry Scan' : 'Verify Attendance'}
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ---------------- FULL-SCREEN SURVEILLANCE OVERLAY ---------------- */}
      {isSurveillanceActive && (
        <div style={{ position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh', background: '#000', zIndex: 3000, display: 'flex', flexDirection: 'column' }}>
          
          <div style={{ padding: '15px 30px', background: '#111', color: 'white', display: 'flex', justifyContent: 'space-between', alignItems: 'center', zIndex: 3010 }}>
            <h2 style={{ margin: 0, color: '#ffcb05' }}>🔴 Live Classroom Tracking</h2>
            <button onClick={stopSurveillance} style={{ background: '#dc3545', color: 'white', border: 'none', padding: '10px 25px', borderRadius: '5px', fontWeight: 'bold', cursor: 'pointer' }}>
              Close Tracker
            </button>
          </div>

          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#000', overflow: 'hidden' }}>
            
            <div style={{ position: 'relative', width: '100%', maxHeight: '100%', aspectRatio: '16/9', display: 'flex', justifyContent: 'center' }}>
              <Webcam 
                ref={surveillanceWebcamRef} 
                audio={false} 
                mirrored={false} 
                screenshotFormat="image/jpeg" 
                videoConstraints={SURVEILLANCE_CONSTRAINTS} 
                style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', objectFit: 'contain' }}
              />
              <canvas 
                ref={canvasRef} 
                width={AI_W} 
                height={AI_H}
                onClick={handleCanvasClick}
                onContextMenu={handleCanvasRightClick}
                onWheel={handleCanvasWheel}
                style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', objectFit: 'contain', zIndex: 10, cursor: 'crosshair' }} 
              />
            </div>

            {/* 🔍 INSPECT OVERLAY */}
            {inspectMode && (
              <div style={{
                position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
                width: '640px', height: '480px', border: '4px solid #ffcb05',
                borderRadius: '12px', overflow: 'hidden', zIndex: 4000,
                boxShadow: '0 20px 60px rgba(0,0,0,0.9)', background: '#000'
              }}>
                <canvas ref={inspectCanvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
                <div style={{
                  position: 'absolute', bottom: 0, left: 0, width: '100%',
                  background: 'rgba(0,0,0,0.85)', color: 'white', padding: '15px',
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  fontSize: '14px', fontWeight: 'bold'
                }}>
                  <span>🔍 Inspect | {inspectMode.scale.toFixed(1)}x</span>
                  <div style={{ display: 'flex', gap: '10px' }}>
                    <button 
                      onClick={assignFromInspect}
                      style={{
                        background: '#28a745', color: 'white', border: 'none',
                        padding: '8px 20px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold'
                      }}
                    >
                      ✅ Assign Name
                    </button>
                    <button onClick={() => setInspectMode(null)} style={{
                      background: '#dc3545', color: 'white', border: 'none',
                      padding: '8px 16px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold'
                    }}>
                      Close
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* 🖱️ WHEEL ZOOM OVERLAY — ADDED */}
            {wheelZoom && (
              <div style={{
                position: 'absolute',
                bottom: '20px',
                right: '20px',
                width: '640px',
                height: '520px',
                background: 'rgba(0,0,0,0.95)',
                border: '3px solid #ffcb05',
                borderRadius: '12px',
                zIndex: 3500,
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
                boxShadow: '0 20px 60px rgba(0,0,0,0.9)'
              }}>
                <canvas 
                  id="wheel-zoom-canvas" 
                  width={640} 
                  height={480} 
                  style={{ width: '100%', height: '480px', display: 'block', cursor: 'crosshair' }} 
                />
                <div style={{
                  padding: '12px',
                  background: '#1a1a2e',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center'
                }}>
                  <span style={{ color: '#ffcb05', fontWeight: 'bold', fontSize: '14px' }}>
                    {wheelZoom.trackId ? `🔍 Tracking: ${wheelZoom.scale.toFixed(1)}x` : '🎯 Manual Zoom Area'}
                  </span>
                  <div style={{ display: 'flex', gap: '10px' }}>
                    {wheelZoom.trackId ? (
                      <button onClick={captureWheelZoomTracked} style={{
                        background: '#28a745', color: 'white', border: 'none',
                        padding: '8px 18px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold'
                      }}>
                        ✅ Assign Name
                      </button>
                    ) : (
                      <button onClick={captureManualZoom} style={{
                        background: '#28a745', color: 'white', border: 'none',
                        padding: '8px 18px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold'
                      }}>
                        📷 Capture Face
                      </button>
                    )}
                    <button onClick={() => setWheelZoom(null)} style={{
                      background: '#dc3545', color: 'white', border: 'none',
                      padding: '8px 18px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold'
                    }}>
                      Close
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* RIGHT PANEL — Live Enroll Assignment */}
            {liveZoom && (
              <div style={{ 
                position: 'absolute', right: 0, top: 0, width: '400px', height: '100%', 
                background: 'rgba(20,20,30,0.95)', zIndex: 4000, padding: '20px', 
                borderLeft: '4px solid #ffcb05', display: 'flex', flexDirection: 'column'
              }}>
                <h2 style={{color: 'white', marginTop: 0}}>⚡ Live Enroll</h2>
                <p style={{color: '#aaa', fontSize: '14px', marginBottom: '20px'}}>
                  {liveZoom.isManual ? '🎯 Manual zoom capture. Select student to register face DNA.' : 'Click student name to register their face DNA!'}
                </p>
                
                <div className="student-select-list" style={{flex: 1, border: '1px solid #444', borderRadius: '8px', background: '#2a2a3c', overflowY: 'auto', padding: '10px'}}>
                  {students.map((student, idx) => (
                    <div key={idx} className="student-select-item" style={{borderBottomColor: '#444', color: 'white'}} onClick={() => assignLiveEnroll(student['Student ID'], student['Student Name'])}>
                      <span><b>{student['Student ID']}</b> - {student['Student Name']}</span>
                      <span style={{color: '#ffcb05'}}>Assign ➔</span>
                    </div>
                  ))}
                </div>
                <button className="btn-cancel" style={{width: '100%', marginTop: '20px', padding: '15px'}} onClick={() => { setLiveZoom(null); setQuickEnrollData(null); }}>
                  Cancel
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ---------------- 1-ON-1 VERIFICATION MODAL ---------------- */}
      {isVerifyModalOpen && verifyingStudent && (
        <div className="modal-overlay" style={{zIndex: 4000}}>
          <div className="modal-content">
            <h2 className="modal-header">Verify Identity</h2>
            <h3 style={{marginTop: 0, color: '#555'}}>Target Student: {verifyingStudent['Student Name']}</h3>
            <div className="webcam-container" style={{minHeight: '250px'}}>
              <Webcam audio={false} ref={webcamRef} mirrored={false} screenshotFormat="image/jpeg" width="100%" videoConstraints={ENROLL_CONSTRAINTS} />
              <div className="webcam-mask" style={{width: '180px', height: '240px'}}></div>
            </div>
            <div style={{margin: '15px 0', fontSize: '18px', fontWeight: 'bold', color: verifyResult.includes('❌') ? '#dc3545' : '#28a745'}}>{verifyResult}</div>
            <button className="btn-capture" onClick={runVerificationScan}>🔍 Scan Face</button>
            <button className="btn-cancel" onClick={() => setIsVerifyModalOpen(false)}>Close</button>
          </div>
        </div>
      )}

    </div>
  );
}

export default App;