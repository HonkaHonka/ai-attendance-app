import React, { useState, useRef, useEffect } from 'react';
import Webcam from "react-webcam";
import './App.css';

const API_BASE = "http://127.0.0.1:8000/api";
const WS_BASE = "ws://127.0.0.1:8000/ws";

// 🚀 Camera constraints - use 1080p to prevent Windows auto-zoom triggering 4K
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
  const [view, setView] = useState('login'); 
  const [email, setEmail] = useState('');
  const [facultyName, setFacultyName] = useState('');
  const [classes, setClasses] = useState([]);
  const [students, setStudents] = useState([]);
  const [selectedClass, setSelectedClass] = useState('');
  const [error, setError] = useState('');

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [enrollStep, setEnrollStep] = useState(''); 
  const [capturedImages, setCapturedImages] = useState({});
  const [isCapturing, setIsCapturing] = useState(false); 
  
  const [isVerifyModalOpen, setIsVerifyModalOpen] = useState(false);
  const [verifyResult, setVerifyResult] = useState('');
  const [verifyingStudent, setVerifyingStudent] = useState(null); 
  const [attendanceRecords, setAttendanceRecords] = useState({}); 

  const [isSurveillanceActive, setIsSurveillanceActive] = useState(false);
  const [detectedFaces, setDetectedFaces] = useState([]);
  const [quickEnrollData, setQuickEnrollData] = useState(null); 
  const [liveZoom, setLiveZoom] = useState(null); 
  const [inspectMode, setInspectMode] = useState(null);
  
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

  const startEnrollment = async (classNbr) => {
    setSelectedClass(classNbr);
    setCapturedImages({});
    setEnrollStep('front'); 
    setIsModalOpen(true); 
    try {
      const res = await fetch(`${API_BASE}/students?email=${encodeURIComponent(email)}&class_nbr=${classNbr}`);
      setStudents(await res.json());
    } catch (err) {}
  };

  const captureBurst = async () => {
    setIsCapturing(true);
    const frames = [];
    for (let i = 0; i < 3; i++) {
      const imageSrc = webcamRef.current.getScreenshot();
      if (imageSrc) frames.push(imageSrc);
      await new Promise(resolve => setTimeout(resolve, 300));
    }
    if (enrollStep === 'front') { setCapturedImages({ ...capturedImages, front: frames }); setEnrollStep('left'); } 
    else if (enrollStep === 'left') { setCapturedImages({ ...capturedImages, left: frames }); setEnrollStep('right'); } 
    else if (enrollStep === 'right') { setCapturedImages({ ...capturedImages, right: frames }); setEnrollStep('select_student'); }
    setIsCapturing(false);
  };

  const assignFaceToStudent = async (studentId, studentName) => {
    setEnrollStep('saving');
    const payload = { student_id: String(studentId), student_name: studentName, class_nbr: String(selectedClass), images: capturedImages };
    try {
      const response = await fetch(`${API_BASE}/enroll-face`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Failed to process face");
      alert(`✅ ${result.message}`);
      setIsModalOpen(false); 
    } catch (error) {
      alert(`❌ AI Error: ${error.message}`);
      setEnrollStep('select_student'); 
    }
  };

  const runVerificationScan = async () => {
    setVerifyResult('Scanning...');
    const imageSrc = webcamRef.current.getScreenshot();
    try {
      const response = await fetch(`${API_BASE}/verify-face`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image: imageSrc }) });
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

  // 🔍 INSPECT CANVAS RENDERER (right-click zoom) - NO CSS TRANSFORM
        // 🔍 INSPECT CANVAS RENDERER (right-click zoom) - NO CSS TRANSFORM
    useEffect(() => {
    let animId;
    
    const drawInspect = () => {
      if (!inspectMode || !inspectCanvasRef.current || !surveillanceWebcamRef.current?.video) {
        animId = requestAnimationFrame(drawInspect);
        return;
      }
      
      const video = surveillanceWebcamRef.current.video;
      const canvas = inspectCanvasRef.current;
      const ctx = canvas.getContext('2d');
      
      // 🎯 LIVE video dimensions
      const vidW = video.videoWidth || 1920;
      const vidH = video.videoHeight || 1080;
      
      const { scale, trackId } = inspectMode;
      
      // 🎯 FIND BY TRACK_ID: Follow the person, not the position
      const liveFace = detectedFaces.find(f => f.track_id === trackId);
      
      if (!liveFace) {
        // Person left frame or tracking lost - draw "TRACKING LOST" message
        canvas.width = 640;
        canvas.height = 480;
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, 640, 480);
        ctx.fillStyle = '#ffcb05';
        ctx.font = 'bold 24px Arial';
        ctx.textAlign = 'center';
        ctx.fillText('🔍 TRACKING LOST', 320, 240);
        ctx.fillStyle = '#aaa';
        ctx.font = '16px Arial';
        ctx.fillText('Student moved out of frame', 320, 270);
        
        animId = requestAnimationFrame(drawInspect);
        return;
      }
      
      const bodyBox = liveFace.box;
      const faceBox = liveFace.face_box || null;
      
      const outputW = 640;
      const outputH = 480;
      
      // Source crop size in VIDEO coordinates
      const srcW = (outputW / scale) * (vidW / AI_W);
      const srcH = (outputH / scale) * (vidH / AI_H);
      
      // 🎯 Center on FACE (upper 25% of body if no face_box)
      let centerX, centerY;
      if (faceBox) {
        centerX = (faceBox[0] + faceBox[2] / 2) * (vidW / AI_W);
        centerY = (faceBox[1] + faceBox[3] / 2) * (vidH / AI_H);
      } else {
        centerX = (bodyBox[0] + bodyBox[2] / 2) * (vidW / AI_W);
        centerY = (bodyBox[1] + bodyBox[3] * 0.25) * (vidH / AI_H);
      }
      
      const srcX = Math.max(0, Math.min(vidW - srcW, centerX - srcW / 2));
      const srcY = Math.max(0, Math.min(vidH - srcH, centerY - srcH / 2));
      
      canvas.width = outputW;
      canvas.height = outputH;
      ctx.drawImage(video, srcX, srcY, srcW, srcH, 0, 0, outputW, outputH);
      
      // 🎯 Draw face outline
      if (faceBox) {
        const [fx, fy, fw, fh] = faceBox;
        const sx = ((fx * (vidW / AI_W)) - srcX) * (outputW / srcW);
        const sy = ((fy * (vidH / AI_H)) - srcY) * (outputH / srcH);
        const sw = fw * (vidW / AI_W) * (outputW / srcW);
        const sh = fh * (vidH / AI_H) * (outputH / srcH);
        ctx.strokeStyle = '#00ff00';
        ctx.lineWidth = 3;
        ctx.strokeRect(sx, sy, sw, sh);
      }
      
      // 🎯 Draw body box outline (dim) to show tracking area
      const [bx, by, bw, bh] = bodyBox;
      const bsx = ((bx * (vidW / AI_W)) - srcX) * (outputW / srcW);
      const bsy = ((by * (vidH / AI_H)) - srcY) * (outputH / srcH);
      const bsw = bw * (vidW / AI_W) * (outputW / srcW);
      const bsh = bh * (vidH / AI_H) * (outputH / srcH);
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 5]);
      ctx.strokeRect(bsx, bsy, bsw, bsh);
      ctx.setLineDash([]);
      
      animId = requestAnimationFrame(drawInspect);
    };
    
    drawInspect();
    return () => cancelAnimationFrame(animId);
  }, [inspectMode, detectedFaces]);

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
        
        // 🎯 Send BODY BOX — backend expects this for head crop
        const assignBox = face.box;  // ← CHANGED from face.face_box || face.box
        
        setQuickEnrollData({ image: tempCanvas.toDataURL('image/jpeg', 0.8), box: assignBox });
        setLiveZoom({ origBox: assignBox });
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
          trackId: face.track_id,  // 🎯 Store track_id, not box position
          scale: zoomScale
        });
      }
    });
  };

  const handleInspectWheel = (e) => {
    if (!inspectMode) return;
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.3 : -0.3;
    setInspectMode(prev => ({
      ...prev,
      scale: Math.max(1.0, Math.min(4.0, prev.scale + delta))
    }));
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
    
    // 🎯 Send BODY BOX (not face_box) — backend expects body box for head crop
    const assignBox = liveFace.box;  // ← CHANGED from liveFace.face_box || liveFace.box
    
    setQuickEnrollData({ 
      image: tempCanvas.toDataURL('image/jpeg', 0.8),
      box: assignBox 
    });
    setLiveZoom({ origBox: assignBox });
    setInspectMode(null);
  };

        const assignLiveEnroll = async (studentId, studentName) => {
    try {
      const box = liveZoom.origBox;

      const response = await fetch(`${API_BASE}/assign-face`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            student_id: String(studentId),
            student_name: studentName,
            image: quickEnrollData.image,
            box: box
          })
      });
      
      let result;
      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        result = await response.json();
      } else {
        const text = await response.text();
        throw new Error(text || `Server error: ${response.status}`);
      }
      
      if (!response.ok) throw new Error(result.detail || result.message || 'Assignment failed');
      
      // 🛡️ Handle duplicate face error
      if (result.status === "error") {
        alert(`❌ ${result.message}`);
        return; // Don't close panel, let teacher try someone else
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

  const closeModal = () => setIsModalOpen(false);

  return (
    <div>
      <div className="top-bar">
        <div>✉ info@lu.ac.ae &nbsp;&nbsp; 📞 600 500606</div>
        <div className="top-bar-right"><span>Our Campuses</span> <span>LU Connect</span> <span>Library Portal</span></div>
      </div>
      <nav className="main-nav">
        <div className="logo">🛡️ Liwa <span>University</span></div>
        <div className="nav-links"><a>Home</a><a>Study</a><a>Admissions</a><a>Research</a><a>Student Life</a><a>About Us</a></div>
      </nav>

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
                      <td style={{textAlign: 'center', display: 'flex', gap: '10px', justifyContent: 'center'}}>
                        <button className="btn-enroll-small" onClick={() => startEnrollment(cls['Class Nbr'])}>📷 Enroll Face</button>
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
              <p style={{color: '#666', fontSize: '16px', marginBottom: '20px'}}>YOLOv8 + PyTorch crowd tracking. <b>Left-click Red boxes to assign</b> | <b>Right-click to inspect/zoom</b></p>
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
            <h2 style={{ margin: 0, color: 'var(--accent-gold)' }}>🔴 Live Classroom Tracking</h2>
            <button onClick={stopSurveillance} style={{ background: '#dc3545', color: 'white', border: 'none', padding: '10px 25px', borderRadius: '5px', fontWeight: 'bold', cursor: 'pointer' }}>
              Close Tracker
            </button>
          </div>

          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#000', overflow: 'hidden' }}>
            
            {/* NO CSS TRANSFORM - pure canvas crop for performance */}
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
                onWheel={handleInspectWheel}
                style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', objectFit: 'contain', zIndex: 10, cursor: 'crosshair' }} 
              />
            </div>

            {/* 🔍 INSPECT OVERLAY - Canvas crop zoom, NO CSS transform */}
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
                  <span>🔍 Inspect | Wheel: zoom | {inspectMode.scale.toFixed(1)}x</span>
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

            {/* RIGHT PANEL - Assignment (opens on left click) */}
            {liveZoom && (
              <div style={{ 
                position: 'absolute', right: 0, top: 0, width: '400px', height: '100%', 
                background: 'rgba(20,20,30,0.95)', zIndex: 4000, padding: '20px', 
                borderLeft: '4px solid var(--accent-gold)', display: 'flex', flexDirection: 'column'
              }}>
                <h2 style={{color: 'white', marginTop: 0}}>⚡ Live Enroll</h2>
                <p style={{color: '#aaa', fontSize: '14px', marginBottom: '20px'}}>Click student name to register their face DNA!</p>
                
                <div className="student-select-list" style={{flex: 1, border: '1px solid #444', borderRadius: '8px', background: '#2a2a3c', overflowY: 'auto', padding: '10px'}}>
                  {students.map((student, idx) => (
                    <div key={idx} className="student-select-item" style={{borderBottomColor: '#444', color: 'white'}} onClick={() => assignLiveEnroll(student['Student ID'], student['Student Name'])}>
                      <span><b>{student['Student ID']}</b> - {student['Student Name']}</span>
                      <span style={{color: 'var(--accent-gold)'}}>Assign ➔</span>
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

      {/* ---------------- ENROLLMENT MODAL (9-Image Burst) ---------------- */}
      {isModalOpen && (
        <div className="modal-overlay">
          <div className="modal-content">
            <h2 className="modal-header">Biometric Enrollment</h2>
            {(enrollStep === 'front' || enrollStep === 'left' || enrollStep === 'right') && (
              <>
                <p>Please align the student's face within the oval.</p>
                <div className="webcam-container">
                  <Webcam audio={false} ref={webcamRef} mirrored={false} screenshotFormat="image/jpeg" width="100%" videoConstraints={ENROLL_CONSTRAINTS} />
                  <div className="webcam-mask"></div>
                  <div className="webcam-overlay-text">
                    {enrollStep === 'front' && "👤 Look straight into the camera"}
                    {enrollStep === 'left' && "⬅️ Turn head slightly to the LEFT"}
                    {enrollStep === 'right' && "➡️ Turn head slightly to the RIGHT"}
                  </div>
                </div>
                <button className="btn-capture" onClick={captureBurst} disabled={isCapturing} style={{ opacity: isCapturing ? 0.7 : 1 }}>
                  {isCapturing ? "📸 Capturing Burst..." : "📸 Capture Image"}
                </button>
                <button className="btn-cancel" onClick={closeModal}>Cancel</button>
              </>
            )}
            {enrollStep === 'select_student' && (
              <>
                <h3>✅ Burst Images Captured!</h3>
                <p>Who is this student? Select their name below:</p>
                <div className="student-select-list">
                  {students.map((student, idx) => (
                    <div key={idx} className="student-select-item" onClick={() => assignFaceToStudent(student['Student ID'], student['Student Name'])}>
                      <span><b>{student['Student ID']}</b> - {student['Student Name']}</span>
                      <span style={{color: 'var(--accent-gold)'}}>Assign ➔</span>
                    </div>
                  ))}
                </div>
                <button className="btn-cancel" onClick={closeModal}>Cancel</button>
              </>
            )}
            {enrollStep === 'saving' && (
              <div style={{padding: '50px'}}><h3>⏳ Extracting Face DNA...</h3><p>AI is processing 9 captured frames...</p></div>
            )}
          </div>
        </div>
      )}

    </div>
  );
}

export default App;