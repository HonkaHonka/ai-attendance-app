import React, { useState, useRef, useEffect } from 'react';
import Webcam from "react-webcam";
import './App.css';

const API_BASE = "http://127.0.0.1:8000/api";
const WS_BASE = "ws://127.0.0.1:8000/ws";

// 🚀 MANDATORY: Guarantees the AI receives high-quality frames and disables TV hardware auto-zoom!
const VIDEO_CONSTRAINTS = {
  width: 1280,
  height: 720,
  facingMode: "user" 
};

function App() {
  const [view, setView] = useState('login'); 
  const [email, setEmail] = useState('');
  const [facultyName, setFacultyName] = useState('');
  const [classes, setClasses] = useState([]);
  const [students, setStudents] = useState([]);
  const [selectedClass, setSelectedClass] = useState('');
  const [error, setError] = useState('');

  const[isModalOpen, setIsModalOpen] = useState(false);
  const [enrollStep, setEnrollStep] = useState(''); 
  const [capturedImages, setCapturedImages] = useState({});
  const [isCapturing, setIsCapturing] = useState(false); 
  
  const [isVerifyModalOpen, setIsVerifyModalOpen] = useState(false);
  const [verifyResult, setVerifyResult] = useState('');
  const [verifyingStudent, setVerifyingStudent] = useState(null); 
  const [attendanceRecords, setAttendanceRecords] = useState({}); 

  const[isSurveillanceActive, setIsSurveillanceActive] = useState(false);
  const [detectedFaces, setDetectedFaces] = useState([]);
  const [quickEnrollData, setQuickEnrollData] = useState(null); 
  
  // 🚀 CINEMATIC ZOOM STATE
  const [liveZoom, setLiveZoom] = useState(null); 
  
  const webcamRef = useRef(null);
  const canvasRef = useRef(null);
  const wsRef = useRef(null);
  const surveillanceWebcamRef = useRef(null); 
  const zoomCanvasRef = useRef(null);
  const pipCanvasRef = useRef(null); 

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
      const data = await res.json();
      setClasses(data);
      setView('classes'); 
      stopSurveillance(); 
    } catch (err) { alert("Error loading classes"); }
  };

  const fetchStudents = async (classNbr) => {
    try {
      const res = await fetch(`${API_BASE}/students?email=${encodeURIComponent(email)}&class_nbr=${classNbr}`);
      const data = await res.json();
      setStudents(data);
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
    const frames =[];
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
        setAttendanceRecords(prev => ({ ...prev,[verifyingStudent['Student ID']]: 'failed' }));
      }
    } catch (error) { setVerifyResult(`⚠️ Error: ${error.message}`); }
  };

  const sendFrameToWebSocket = () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && surveillanceWebcamRef.current) {
      const imageSrc = surveillanceWebcamRef.current.getScreenshot();
      if (imageSrc) {
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
      
      wsRef.current.onopen = () => {
        console.log("WebSocket Connected!");
        sendFrameToWebSocket(); 
      };

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

      wsRef.current.onerror = (error) => { stopSurveillance(); };
    }
  };

  const stopSurveillance = () => {
    setIsSurveillanceActive(false);
    if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
    setDetectedFaces([]);
    setLiveZoom(null);
    setQuickEnrollData(null);
  };

  useEffect(() => { return () => stopSurveillance(); },[]);

  // ==========================================
  // 🚀 RESTORED: PERFECT ZERO-MATH DRAWING FROM VERSION 1
  // ==========================================
  useEffect(() => {
    if (canvasRef.current && surveillanceWebcamRef.current && surveillanceWebcamRef.current.video) {
      const video = surveillanceWebcamRef.current.video;
      const canvas = canvasRef.current;
      
      if (video.videoWidth > 0 && video.videoHeight > 0) {
        canvas.width = video.clientWidth;
        canvas.height = video.clientHeight;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        const scaleX = video.clientWidth / video.videoWidth;
        const scaleY = video.clientHeight / video.videoHeight;

        detectedFaces.forEach(face => {
          const[origX, origY, origW, origH] = face.box;
          const x = origX * scaleX;
          const y = origY * scaleY;
          const w = origW * scaleX;
          const h = origH * scaleY;

          ctx.beginPath();
          ctx.lineWidth = 4;
          
          if (face.status === 'known') { ctx.strokeStyle = '#28a745'; ctx.fillStyle = '#28a745'; } 
          else if (face.status === 'scanning') { ctx.strokeStyle = '#ffcb05'; ctx.fillStyle = '#ffcb05'; }
          else { ctx.strokeStyle = '#dc3545'; ctx.fillStyle = '#dc3545'; }
          
          ctx.rect(x, y, w, h);
          ctx.stroke();

          ctx.font = 'bold 20px Arial';
          const text = face.name;
          const textWidth = ctx.measureText(text).width;
          ctx.fillRect(x, y - 35, textWidth + 20, 35);
          ctx.fillStyle = '#fff';
          ctx.fillText(text, x + 10, y - 8);
        });
      }
    }
  }, [detectedFaces]);

  // ==========================================
  // 🚀 RESTORED: PiP (PICTURE-IN-PICTURE) FROM VERSION 2
  // ==========================================
  useEffect(() => {
    let animId;
    const drawPiP = () => {
      if (liveZoom && pipCanvasRef.current && surveillanceWebcamRef.current && surveillanceWebcamRef.current.video) {
        const video = surveillanceWebcamRef.current.video;
        const ctx = pipCanvasRef.current.getContext('2d');
        
        if (video.readyState === 4 && video.videoWidth > 0) {
          pipCanvasRef.current.width = video.videoWidth;
          pipCanvasRef.current.height = video.videoHeight;
          ctx.drawImage(video, 0, 0, video.videoWidth, video.videoHeight);
        }
      }
      animId = requestAnimationFrame(drawPiP);
    };
    drawPiP();
    return () => cancelAnimationFrame(animId);
  }, [liveZoom]);


  // ==========================================
  // 🚀 MERGED: FLAWLESS CLICK MATH (V1) + LIVE ZOOM UI (V2)
  // ==========================================
  const handleCanvasClick = (e) => {
    if (liveZoom) return;

    const canvas = canvasRef.current;
    if (!canvas || !surveillanceWebcamRef.current || !surveillanceWebcamRef.current.video) return;
    
    const rect = canvas.getBoundingClientRect();
    const video = surveillanceWebcamRef.current.video;
    
    const scaleX = video.videoWidth / rect.width;
    const scaleY = video.videoHeight / rect.height;
    
    const clickX = (e.clientX - rect.left) * scaleX;
    const clickY = (e.clientY - rect.top) * scaleY;

    detectedFaces.forEach(face => {
      const[origX, origY, origW, origH] = face.box;
      
      // Allow clicking on Unknown (Red) OR Scanning (Yellow) boxes
      if ((face.status === 'unknown' || face.status === 'scanning') && 
          clickX >= origX && clickX <= origX + origW && 
          clickY >= origY && clickY <= origY + origH) {
        
        // Target lock the center of the face box!
        const originX = ((origX + origW / 2) / video.videoWidth) * 100;
        const originY = ((origY + origH / 2) / video.videoHeight) * 100;

        setLiveZoom({
           origBox: face.box,
           cssOrigin: `${originX}% ${originY}%`
        });
        
        const frame = surveillanceWebcamRef.current.getScreenshot();
        setQuickEnrollData({ image: frame, box: face.box });
      }
    });
  };

  // ==========================================
  // 🚀 THE DIGITAL PORTRAIT FIX (ID Badge style)
  // ==========================================
  useEffect(() => {
    if (quickEnrollData && zoomCanvasRef.current) {
      const img = new Image();
      img.onload = () => {
        const ctx = zoomCanvasRef.current.getContext('2d');
        const[x, y, w, h] = quickEnrollData.box;
        
        // We know Python is already sending a tight face box.
        // We just add a slight 30% padding so it looks like a nice ID portrait,
        // preventing the "full body" squeeze!
        const padX = w * 0.3; 
        const padY = h * 0.3; 
        
        const sx = Math.max(0, x - padX);
        const sy = Math.max(0, y - padY);
        const sw = Math.min(img.width - sx, w + (padX * 2));
        const sh = Math.min(img.height - sy, h + (padY * 2));

        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.clearRect(0, 0, 400, 400);
        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, 400, 400);
      };
      img.src = quickEnrollData.image;
    }
  },[quickEnrollData]);

  const assignLiveEnroll = async (studentId, studentName) => {
    try {
      const liveFrame = surveillanceWebcamRef.current.getScreenshot();
      const[x, y, w, h] = liveZoom.origBox;
      
      const pad = w * 0.2;
      const expandedBox =[ Math.max(0, x - pad), Math.max(0, y - pad), w + (pad * 2), h + (pad * 2) ];

      const response = await fetch(`${API_BASE}/assign-face`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            student_id: String(studentId),
            student_name: studentName,
            image: liveFrame,
            box: expandedBox 
          })
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail);
      
      alert(`✅ Successfully Enrolled!`);
      setLiveZoom(null); 
      setQuickEnrollData(null);
      
    } catch (error) { alert(`❌ Quick Enroll Error: ${error.message}`); }
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

      {/* ---------------- LOGIN VIEW ---------------- */}
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

      {/* ---------------- FACULTY DASHBOARD ---------------- */}
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

      {/* ---------------- STUDENT LIST ---------------- */}
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
              <p style={{color: '#666', fontSize: '16px', marginBottom: '20px'}}>YOLOv8 + PyTorch crowd tracking. <b>Click on Red (Unknown) boxes</b> to Quick Enroll a student!</p>
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
        <div style={{ position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh', background: '#000', zIndex: 3000, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          
          <div style={{ padding: '15px 30px', background: '#111', color: 'white', display: 'flex', justifyContent: 'space-between', alignItems: 'center', zIndex: 3010 }}>
            <h2 style={{ margin: 0, color: 'var(--accent-gold)' }}>🔴 Live Classroom Tracking</h2>
            <button onClick={stopSurveillance} style={{ background: '#dc3545', color: 'white', border: 'none', padding: '10px 25px', borderRadius: '5px', fontWeight: 'bold', cursor: 'pointer' }}>
              Close Tracker
            </button>
          </div>

          <div style={{ flex: 1, position: 'relative', background: '#000', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
            <div style={{ 
              position: 'relative', width: '100%', maxHeight: '100%', aspectRatio: '16/9', display: 'flex', justifyContent: 'center',
              transform: liveZoom ? `scale(2.5)` : 'scale(1)',
              transformOrigin: liveZoom ? liveZoom.cssOrigin : 'center center',
              transition: 'transform 0.5s cubic-bezier(0.25, 1, 0.5, 1)'
             }}>
              <Webcam 
                ref={surveillanceWebcamRef} 
                audio={false} 
                mirrored={false} 
                screenshotFormat="image/jpeg" 
                videoConstraints={VIDEO_CONSTRAINTS} 
                style={{ width: '100%', height: 'auto', display: 'block' }}
              />
              <canvas 
                ref={canvasRef} 
                onClick={handleCanvasClick} 
                style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 10, cursor: liveZoom ? 'default' : 'crosshair' }} 
              />
            </div>

            {/* 🚀 THE PICTURE-IN-PICTURE & SIDE PANEL */}
            {liveZoom && (
              <>
                <div 
                  onClick={() => { setLiveZoom(null); setQuickEnrollData(null); }} 
                  style={{
                    position: 'absolute', top: '20px', left: '20px', width: '320px', height: '180px', 
                    border: '3px solid white', borderRadius: '8px', overflow: 'hidden',
                    boxShadow: '0 10px 30px rgba(0,0,0,0.8)', cursor: 'pointer', zIndex: 4000,
                    background: '#000'
                  }}>
                  <canvas ref={pipCanvasRef} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
                  <div style={{position: 'absolute', bottom: '5px', left: '0', width: '100%', textAlign: 'center', color: 'white', background: 'rgba(0,0,0,0.6)', padding: '4px 0', fontSize: '13px', fontWeight: 'bold'}}>
                    Click here to Zoom Out
                  </div>
                </div>

                <div style={{ 
                  position: 'absolute', right: 0, top: 0, width: '400px', height: '100%', 
                  background: 'rgba(20,20,30,0.95)', zIndex: 4000, padding: '20px', 
                  borderLeft: '4px solid var(--accent-gold)', display: 'flex', flexDirection: 'column'
                }}>
                  <h2 style={{color: 'white', marginTop: 0}}>⚡ Live Enroll</h2>
                  <p style={{color: '#aaa', fontSize: '14px', marginBottom: '20px'}}>Camera is live. Wait until the student looks at the camera, then click their name below to register their DNA!</p>
                  
                  <div className="student-select-list" style={{flex: 1, border: '1px solid #444', borderRadius: '8px', background: '#2a2a3c', overflowY: 'auto', padding: '10px'}}>
                    {students.map((student, idx) => (
                      <div key={idx} className="student-select-item" style={{borderBottomColor: '#444', color: 'white'}} onClick={() => assignLiveEnroll(student['Student ID'], student['Student Name'])}>
                        <span><b>{student['Student ID']}</b> - {student['Student Name']}</span>
                        <span style={{color: 'var(--accent-gold)'}}>Assign ➔</span>
                      </div>
                    ))}
                  </div>
                  <button className="btn-cancel" style={{width: '100%', marginTop: '20px', padding: '15px'}} onClick={() => { setLiveZoom(null); setQuickEnrollData(null); }}>
                    Cancel & Zoom Out
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ---------------- ENROLLMENT MODAL ---------------- */}
      {isModalOpen && (
        <div className="modal-overlay" style={{zIndex: 4000}}>
          <div className="modal-content">
            <h2 className="modal-header">Biometric Enrollment</h2>
            {(enrollStep === 'front' || enrollStep === 'left' || enrollStep === 'right') && (
              <>
                <p>Please align the student's face within the oval.</p>
                <div className="webcam-container">
                  <Webcam audio={false} ref={webcamRef} mirrored={false} screenshotFormat="image/jpeg" width="100%" videoConstraints={VIDEO_CONSTRAINTS} />
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

      {/* ---------------- 1-ON-1 VERIFICATION MODAL ---------------- */}
      {isVerifyModalOpen && verifyingStudent && (
        <div className="modal-overlay" style={{zIndex: 4000}}>
          <div className="modal-content">
            <h2 className="modal-header">Verify Identity</h2>
            <h3 style={{marginTop: 0, color: '#555'}}>Target Student: {verifyingStudent['Student Name']}</h3>
            <div className="webcam-container" style={{minHeight: '250px'}}>
              <Webcam audio={false} ref={webcamRef} mirrored={false} screenshotFormat="image/jpeg" width="100%" videoConstraints={VIDEO_CONSTRAINTS} />
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