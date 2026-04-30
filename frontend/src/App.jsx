import React, { useState, useRef, useEffect } from 'react';
import Webcam from "react-webcam";
import './App.css';

const API_BASE = "http://127.0.0.1:8000/api";
const WS_BASE = "ws://127.0.0.1:8000/ws";

// 🚀 MANDATORY: Keeps the AI fed with high-quality, perfectly proportioned images
const VIDEO_CONSTRAINTS = {
  width: 1280,
  height: 720,
  facingMode: "user" 
};

function App() {
  const[view, setView] = useState('login'); 
  const[email, setEmail] = useState('');
  const[facultyName, setFacultyName] = useState('');
  const[classes, setClasses] = useState([]);
  const[students, setStudents] = useState([]);
  const[selectedClass, setSelectedClass] = useState('');
  const[error, setError] = useState('');

  const[isModalOpen, setIsModalOpen] = useState(false);
  const[enrollStep, setEnrollStep] = useState(''); 
  const [capturedImages, setCapturedImages] = useState({});
  const[isCapturing, setIsCapturing] = useState(false); 
  
  const[isVerifyModalOpen, setIsVerifyModalOpen] = useState(false);
  const[verifyResult, setVerifyResult] = useState('');
  const[verifyingStudent, setVerifyingStudent] = useState(null); 
  const[attendanceRecords, setAttendanceRecords] = useState({}); 

  const[isSurveillanceActive, setIsSurveillanceActive] = useState(false);
  const[detectedFaces, setDetectedFaces] = useState([]);
  const[quickEnrollData, setQuickEnrollData] = useState(null); 
  
  const webcamRef = useRef(null);
  const canvasRef = useRef(null);
  const wsRef = useRef(null);
  const surveillanceWebcamRef = useRef(null); 
  const zoomCanvasRef = useRef(null);

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
        setAttendanceRecords(prev => ({ ...prev, [verifyingStudent['Student ID']]: 'failed' }));
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
  };

  useEffect(() => { return () => stopSurveillance(); },[]);

  // ==========================================
  // 🚀 PERFECT 16:9 ZERO-MATH DRAWING LOOP
  // ==========================================
  useEffect(() => {
    if (canvasRef.current && surveillanceWebcamRef.current && surveillanceWebcamRef.current.video) {
      const video = surveillanceWebcamRef.current.video;
      const canvas = canvasRef.current;
      
      if (video.videoWidth > 0 && video.videoHeight > 0) {
        // Force the canvas resolution to match the video element's internal CSS size!
        canvas.width = video.clientWidth;
        canvas.height = video.clientHeight;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Simple scale mapping (Because CSS aspectRatio: 16/9 guarantees no black bars!)
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

  const handleCanvasClick = (e) => {
    const canvas = canvasRef.current;
    if (!canvas || !surveillanceWebcamRef.current || !surveillanceWebcamRef.current.video) return;
    
    const rect = canvas.getBoundingClientRect();
    const video = surveillanceWebcamRef.current.video;
    
    // Translate click back to Python's 1280x720 grid
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
        
        // Grab screenshot instantly and launch the Target Lock modal
        const frame = surveillanceWebcamRef.current.getScreenshot();
        setQuickEnrollData({ image: frame, box: face.box });
      }
    });
  };

  // ==========================================
  // 🚀 DIGITAL ZOOM FOR TARGET LOCK MODAL
  // ==========================================
  useEffect(() => {
    if (quickEnrollData && zoomCanvasRef.current) {
      const img = new Image();
      img.onload = () => {
        const ctx = zoomCanvasRef.current.getContext('2d');
        const[x, y, w, h] = quickEnrollData.box;
        
        // Extract just the Head and Shoulders (Top 40% of body box)
        const headH = h * 0.4;
        const padX = w * 0.2;
        const padY = headH * 0.3;
        
        const sx = Math.max(0, x - padX);
        const sy = Math.max(0, y - padY);
        const sw = Math.min(img.width - sx, w + (padX * 2));
        const sh = Math.min(img.height - sy, headH + (padY * 2));

        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.clearRect(0, 0, 400, 400);
        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, 400, 400);
      };
      img.src = quickEnrollData.image;
    }
  },[quickEnrollData]);

  const assignQuickEnroll = async (studentId, studentName) => {
    try {
      const response = await fetch(`${API_BASE}/assign-face`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            student_id: String(studentId),
            student_name: studentName,
            image: quickEnrollData.image,
            box: quickEnrollData.box 
          })
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail);
      
      alert(`✅ Successfully Enrolled!`);
      setQuickEnrollData(null); 
    } catch (error) { alert(`❌ Quick Enroll Error: ${error.message}`); }
  };

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
            <p>Select an action below to manage student enrollment and attendance.</p>
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
        <div style={{ position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh', background: '#000', zIndex: 3000, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
          
          <div style={{ width: '100%', padding: '15px 30px', background: '#111', color: 'white', display: 'flex', justifyContent: 'space-between', alignItems: 'center', zIndex: 3010 }}>
            <h2 style={{ margin: 0, color: 'var(--accent-gold)' }}>🔴 Live Classroom Tracking</h2>
            <button onClick={stopSurveillance} style={{ background: '#dc3545', color: 'white', border: 'none', padding: '10px 25px', borderRadius: '5px', fontWeight: 'bold', cursor: 'pointer' }}>
              Close Tracker
            </button>
          </div>

          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', width: '100%' }}>
            
            {/* 🚀 THE FIX: Strict 16:9 Container forces the video and canvas to align flawlessly without stretching or cropping! */}
            <div style={{ position: 'relative', width: '100%', maxHeight: '100%', aspectRatio: '16/9', display: 'flex', justifyContent: 'center' }}>
              <Webcam 
                ref={surveillanceWebcamRef} 
                audio={false} 
                mirrored={false} 
                screenshotFormat="image/jpeg" 
                videoConstraints={VIDEO_CONSTRAINTS} 
                style={{ width: '100%', height: '100%', objectFit: 'contain', position: 'absolute' }}
              />
              <canvas 
                ref={canvasRef} 
                onClick={handleCanvasClick} 
                style={{ position: 'absolute', width: '100%', height: '100%', zIndex: 10, cursor: 'crosshair' }} 
              />
            </div>
          </div>
        </div>
      )}

      {/* ---------------- TARGET LOCKED INVESTIGATION ZOOM UI ---------------- */}
      {quickEnrollData && (
        <div className="modal-overlay" style={{zIndex: 5000, backdropFilter: 'blur(10px)'}}>
          <div className="modal-content" style={{maxWidth: '850px', display: 'flex', gap: '30px', alignItems: 'flex-start', background: '#1e1e2d', color: 'white', border: '2px solid #3e426b'}}>
            
            {/* LEFT: The High-Res Zoomed In View */}
            <div style={{flex: 1, textAlign: 'center'}}>
              <h3 style={{color: 'var(--accent-gold)', marginTop: 0}}>🎯 Target Locked</h3>
              <canvas 
                ref={zoomCanvasRef} 
                width="400" height="400" 
                style={{borderRadius: '12px', border: '4px solid var(--accent-gold)', objectFit: 'cover', boxShadow: '0 10px 30px rgba(0,0,0,0.5)', width: '100%', background: '#000'}} 
              />
              <p style={{color: '#aaa', fontSize: '14px', marginTop: '15px'}}>Ensure face is clearly visible in the preview.</p>
            </div>

            {/* RIGHT: Name Selection Panel */}
            <div style={{flex: 1, textAlign: 'left'}}>
              <h2 className="modal-header" style={{marginTop: 0, borderBottomColor: '#444'}}>⚡ Quick Enroll</h2>
              <p style={{color: '#ccc'}}>Who is this student?</p>
              
              <div className="student-select-list" style={{maxHeight: '350px', border: '1px solid #444', borderRadius: '8px', background: '#2a2a3c', overflowY: 'auto', padding: '10px'}}>
                {students.map((student, idx) => (
                  <div key={idx} className="student-select-item" style={{borderBottomColor: '#444', color: 'white'}} onClick={() => assignQuickEnroll(student['Student ID'], student['Student Name'])}>
                    <span><b>{student['Student ID']}</b> - {student['Student Name']}</span>
                    <span style={{color: 'var(--accent-gold)'}}>Assign ➔</span>
                  </div>
                ))}
              </div>
              <br/>
              <button className="btn-cancel" style={{width: '100%'}} onClick={() => setQuickEnrollData(null)}>Cancel</button>
            </div>

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

      {/* ---------------- ENROLLMENT MODAL (9-Image Burst) ---------------- */}
      {isModalOpen && (
        <div className="modal-overlay">
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

    </div>
  );
}

export default App;