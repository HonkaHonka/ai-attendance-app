import React, { useState, useRef, useEffect } from 'react';
import Webcam from "react-webcam";
import './App.css';

const API_BASE = "http://127.0.0.1:8000/api";
const WS_BASE = "ws://127.0.0.1:8000/ws";

// 🚀 FIX 1: We request 1080p max. This stops the TV from crashing the WebSocket with 8.3MP 4K images!
const SURVEILLANCE_CONSTRAINTS = {
  facingMode: "user",
  width: { ideal: 1920 },
  height: { ideal: 1080 }
};

// 720p for the small enrollment modals to save memory
const ENROLL_CONSTRAINTS = {
  facingMode: "user",
  width: { ideal: 1280 },
  height: { ideal: 720 }
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
  const[capturedImages, setCapturedImages] = useState({});
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
        setAttendanceRecords(prev => ({ ...prev,[verifyingStudent['Student ID']]: 'failed' }));
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
  };

  useEffect(() => { return () => stopSurveillance(); },[]);

  // ==========================================
  // 🚀 FIX 2: ZERO-MATH PERFECT OVERLAY
  // ==========================================
  useEffect(() => {
    if (canvasRef.current && surveillanceWebcamRef.current && surveillanceWebcamRef.current.video) {
      const video = surveillanceWebcamRef.current.video;
      const canvas = canvasRef.current;
      
      if (video.videoWidth > 0 && video.videoHeight > 0) {
        // We set the canvas internal resolution to the exact Native Camera Resolution.
        // We do absolutely zero scaling math. Python sent native coordinates, we draw native coordinates!
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        detectedFaces.forEach(face => {
          const[x, y, w, h] = face.box;

          ctx.beginPath();
          // Scale line thickness up slightly for 1080p
          ctx.lineWidth = 6; 
          
          if (face.status === 'known') { ctx.strokeStyle = '#28a745'; ctx.fillStyle = '#28a745'; } 
          else if (face.status === 'scanning') { ctx.strokeStyle = '#ffcb05'; ctx.fillStyle = '#ffcb05'; }
          else { ctx.strokeStyle = '#dc3545'; ctx.fillStyle = '#dc3545'; }
          
          ctx.rect(x, y, w, h);
          ctx.stroke();

          ctx.font = 'bold 30px Arial';
          const text = face.name;
          const textWidth = ctx.measureText(text).width;
          ctx.fillRect(x, y - 40, textWidth + 20, 40);
          ctx.fillStyle = '#fff';
          ctx.fillText(text, x + 10, y - 10);
        });
      }
    }
  },[detectedFaces]);

  // ==========================================
  // 🚀 FIX 3: REVERSE NATIVE CLICK MAPPING
  // ==========================================
  const handleCanvasClick = (e) => {
    const canvas = canvasRef.current;
    if (!canvas || !surveillanceWebcamRef.current || !surveillanceWebcamRef.current.video) return;
    
    const rect = canvas.getBoundingClientRect();
    const video = surveillanceWebcamRef.current.video;
    
    // We reverse the CSS object-fit: contain to find the native video pixel you clicked
    const vRatio = video.videoWidth / video.videoHeight;
    const cRatio = rect.width / rect.height;
    let renderW = rect.width;
    let renderH = rect.height;
    let offsetX = 0;
    let offsetY = 0;
    
    if (vRatio > cRatio) {
      renderH = rect.width / vRatio;
      offsetY = (rect.height - renderH) / 2;
    } else {
      renderW = rect.height * vRatio;
      offsetX = (rect.width - renderW) / 2;
    }

    const clickX_css = e.clientX - rect.left - offsetX;
    const clickY_css = e.clientY - rect.top - offsetY;

    const nativeX = clickX_css * (video.videoWidth / renderW);
    const nativeY = clickY_css * (video.videoHeight / renderH);

    detectedFaces.forEach(face => {
      const[x, y, w, h] = face.box;
      
      if ((face.status === 'unknown' || face.status === 'scanning') && 
          nativeX >= x && nativeX <= x + w && 
          nativeY >= y && nativeY <= y + h) {
        
        const frame = surveillanceWebcamRef.current.getScreenshot();
        setQuickEnrollData({ image: frame, box: face.box });
        stopSurveillance(); 
      }
    });
  };

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
        <>
          <div className="hero-section">
            <h1 className="hero-title">Liwa University</h1>
            <h2 style={{fontWeight: 'normal', margin: 0}}>Rated for Excellence</h2>
            <div className="hero-stars">★★★★★</div>
            <div className="login-card">
              <h2>Faculty Portal</h2>
              <form onSubmit={handleLogin}>
                <input type="email" placeholder="Enter email (e.g. ihab.awad@lu.ac.ae)" value={email} onChange={(e) => setEmail(e.target.value)} required />
                {error && <p style={{color: 'red', fontSize: '14px', fontWeight: 'bold'}}>{error}</p>}
                <button type="submit" className="btn-primary">Sign In to Portal</button>
              </form>
            </div>
          </div>
          <div className="stats-bar">
            <div className="stat-item"><div className="stat-icon">🏛️</div><div className="stat-text"><h4>Liwa University</h4><p>Licensed by the MOHESR in UAE</p></div></div>
            <div className="stat-item"><div className="stat-icon">🎓</div><div className="stat-text"><h4>Graduate & Undergraduate</h4><p>35 Programs Available</p></div></div>
            <div className="stat-item"><div className="stat-icon">📍</div><div className="stat-text"><h4>Campuses in</h4><p>Abu Dhabi and Al Ain</p></div></div>
          </div>
          <div className="about-section">
            <div className="about-logo-placeholder">🛡️</div>
            <div className="about-content">
              <h2>About Liwa University</h2>
              <p>Since 1993, Liwa University has been a cornerstone of higher education in the Emirate of Abu Dhabi...</p>
              <button className="btn-primary" style={{width: '200px', marginTop: '10px'}}>Read More</button>
            </div>
          </div>
        </>
      )}

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

      {view === 'students' && (
        <div className="app-container">
          <div className="content-box">
            <button className="back-btn" onClick={() => setView('classes')}>← Back to Classes</button>
            
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
              <h2 className="section-title" style={{ display: 'inline-block', margin: 0 }}>Class Roster: {selectedClass}</h2>
              <button 
                style={{ background: '#28a745', color: 'white', padding: '10px 20px', borderRadius: '5px', border: 'none', cursor: 'pointer', fontWeight: 'bold' }}
                onClick={downloadAttendanceReport}
              >
                📥 Download Excel Report
              </button>
            </div>
            
            <div style={{background: '#f8f9fa', padding: '30px', borderRadius: '8px', border: '2px solid #e9ecef', marginBottom: '30px', textAlign: 'center'}}>
              <h3 style={{marginTop: 0, color: 'var(--primary-dark)', fontSize: '24px'}}>🎥 Live Classroom Surveillance</h3>
              <p style={{color: '#666', fontSize: '16px', marginBottom: '20px'}}>
                YOLOv8 + PyTorch crowd tracking. <b>Click on Red (Unknown) boxes</b> to Quick Enroll a student!
              </p>
              <button 
                style={{background: '#2f3254', color: 'white', padding: '15px 40px', borderRadius: '30px', border: 'none', cursor: 'pointer', fontWeight: 'bold', fontSize: '18px', boxShadow: '0 4px 10px rgba(0,0,0,0.2)'}} 
                onClick={toggleSurveillance}
              >
                ▶ Launch Full-Screen Tracker
              </button>
            </div>

            <div style={{maxWidth: '800px', margin: '0 auto'}}>
              <table>
                <thead>
                  <tr>
                    <th>Student ID</th>
                    <th>Student Name</th>
                    <th style={{textAlign: 'center'}}>Attendance Status</th>
                  </tr>
                </thead>
                <tbody>
                  {students.map((student, idx) => {
                    const status = attendanceRecords[student['Student ID']];
                    return (
                      <tr key={idx}>
                        <td>{student['Student ID']}</td>
                        <td>{student['Student Name']}</td>
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
            <button 
              onClick={stopSurveillance} 
              style={{ background: '#dc3545', color: 'white', border: 'none', padding: '10px 25px', borderRadius: '5px', fontWeight: 'bold', cursor: 'pointer' }}>
              Close Tracker
            </button>
          </div>

          <div style={{ flex: 1, position: 'relative', background: '#000', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
            <Webcam 
              ref={surveillanceWebcamRef} 
              audio={false} 
              mirrored={false} 
              screenshotFormat="image/jpeg" 
              videoConstraints={SURVEILLANCE_CONSTRAINTS} 
              style={{ position: 'absolute', width: '100%', height: '100%', objectFit: 'contain' }}
            />
            {/* 🚀 THE MAGIC: Canvas and Video share the exact same CSS sizing. No manual math needed! */}
            <canvas 
              ref={canvasRef} 
              onClick={handleCanvasClick} 
              style={{ position: 'absolute', width: '100%', height: '100%', objectFit: 'contain', zIndex: 10, cursor: 'crosshair' }} 
            />
          </div>
        </div>
      )}

      {/* ---------------- ENROLLMENT MODAL ---------------- */}
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

      {/* ---------------- QUICK ENROLL MODAL ---------------- */}
      {quickEnrollData && (
        <div className="modal-overlay" style={{zIndex: 4000}}>
          <div className="modal-content">
            <h2 className="modal-header">⚡ Quick Live Enrollment</h2>
            <p>You clicked an Unknown face. Who is this student?</p>
            <div className="student-select-list">
              {students.map((student, idx) => (
                <div key={idx} className="student-select-item" onClick={() => assignQuickEnroll(student['Student ID'], student['Student Name'])}>
                  <span><b>{student['Student ID']}</b> - {student['Student Name']}</span>
                  <span style={{color: 'var(--accent-gold)'}}>Assign ➔</span>
                </div>
              ))}
            </div>
            <br/><button className="btn-cancel" onClick={() => setQuickEnrollData(null)}>Cancel</button>
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