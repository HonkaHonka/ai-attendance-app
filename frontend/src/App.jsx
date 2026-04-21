import React, { useState, useRef, useEffect } from 'react';
import Webcam from "react-webcam";
import './App.css';

// Using HTTP for normal stuff, and WS for live streaming
const API_BASE = "http://127.0.0.1:8000/api";
const WS_BASE = "ws://127.0.0.1:8000/ws";

function App() {
  const[view, setView] = useState('login'); 
  const [email, setEmail] = useState('');
  const [facultyName, setFacultyName] = useState('');
  const [classes, setClasses] = useState([]);
  const [students, setStudents] = useState([]);
  const [selectedClass, setSelectedClass] = useState('');
  const [error, setError] = useState('');

  // --- WEBCAM ENROLLMENT STATE ---
  const[isModalOpen, setIsModalOpen] = useState(false);
  const[enrollStep, setEnrollStep] = useState(''); 
  const [capturedImages, setCapturedImages] = useState({});
  const[isCapturing, setIsCapturing] = useState(false); 
  const webcamRef = useRef(null);

  // --- VERIFICATION & ATTENDANCE STATE ---
  const[isVerifyModalOpen, setIsVerifyModalOpen] = useState(false);
  const [verifyResult, setVerifyResult] = useState('');
  const[verifyingStudent, setVerifyingStudent] = useState(null); 
  const[attendanceRecords, setAttendanceRecords] = useState({}); 

  // --- LIVE SURVEILLANCE & QUICK ENROLL STATE ---
  const[isSurveillanceActive, setIsSurveillanceActive] = useState(false);
  const[detectedFaces, setDetectedFaces] = useState([]);
  const [quickEnrollData, setQuickEnrollData] = useState(null); 
  const canvasRef = useRef(null);
  
  // NEW: WebSocket reference
  const wsRef = useRef(null);

  // 1. Handle Login
  const handleLogin = async (e) => {
    e.preventDefault();
    setError('');
    try {
      const res = await fetch(`${API_BASE}/login?email=${encodeURIComponent(email)}`);
      if (!res.ok) throw new Error("Faculty email not found or Server is offline.");
      const data = await res.json();
      setFacultyName(data.name);
      fetchClasses(email);
    } catch (err) {
      setError(err.message);
    }
  };

  const fetchClasses = async (userEmail) => {
    try {
      const res = await fetch(`${API_BASE}/classes?email=${encodeURIComponent(userEmail)}`);
      const data = await res.json();
      setClasses(data);
      setView('classes'); 
      stopSurveillance(); 
    } catch (err) {
      alert("Error loading classes");
    }
  };

  const fetchStudents = async (classNbr) => {
    try {
      const res = await fetch(`${API_BASE}/students?email=${encodeURIComponent(email)}&class_nbr=${classNbr}`);
      const data = await res.json();
      setStudents(data);
      setSelectedClass(classNbr);
      setAttendanceRecords({}); 
      setView('students');
    } catch (err) {
      alert("Error loading students");
    }
  };
  
  const downloadAttendanceReport = async () => {
    try {
      const response = await fetch(`${API_BASE}/export-attendance`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          class_nbr: Number(selectedClass),
          attendance_records: attendanceRecords
        })
      });

      if (!response.ok) throw new Error("Failed to generate report");

      // Convert the response to a downloadable Blob
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `Attendance_Class_${selectedClass}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      alert(`Error downloading report: ${error.message}`);
    }
  };

  const startEnrollment = async (classNbr) => {
    setSelectedClass(classNbr);
    setCapturedImages({});
    setEnrollStep('front'); 
    setIsModalOpen(true); 
    try {
      const res = await fetch(`${API_BASE}/students?email=${encodeURIComponent(email)}&class_nbr=${classNbr}`);
      const data = await res.json();
      setStudents(data);
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

  const runVerificationScan = async () => { /* Kept as is */ };

  // --- NEW: WEBSOCKET SURVEILLANCE LOGIC ---
  // --- NEW: WEBSOCKET SURVEILLANCE LOGIC ---
  const sendFrameToWebSocket = () => {
    // FIX: Only rely on Refs, never on React State variables here!
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && webcamRef.current) {
      const imageSrc = webcamRef.current.getScreenshot();
      if (imageSrc) {
        wsRef.current.send(JSON.stringify({ image: imageSrc }));
      } else {
        // If webcam is still warming up, try again next frame
        requestAnimationFrame(sendFrameToWebSocket);
      }
    }
  };

  const toggleSurveillance = () => {
    if (isSurveillanceActive) {
      stopSurveillance();
    } else {
      setIsSurveillanceActive(true);
      
      // Connect to the WebSocket Endpoint
      wsRef.current = new WebSocket(`${WS_BASE}/surveillance`);
      
      wsRef.current.onopen = () => {
        console.log("WebSocket Connected!");
        sendFrameToWebSocket(); // Send the very first frame
      };

      wsRef.current.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.status === 'success') {
          setDetectedFaces(data.faces);
          
          // Mark attendance
          const newRecords = {};
          data.faces.forEach(face => {
            if (face.status === 'known' && face.student_id) newRecords[face.student_id] = 'present';
          });
          setAttendanceRecords(prev => ({ ...prev, ...newRecords }));
        }
        
        // THE FIX: Instead of checking 'isSurveillanceActive', we just check if the socket is open.
        // This completely prevents the React "Stale Closure" bug!
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          requestAnimationFrame(sendFrameToWebSocket);
        }
      };

      wsRef.current.onerror = (error) => {
        console.error("WebSocket error", error);
        stopSurveillance();
      };
    }
  };

  const stopSurveillance = () => {
    setIsSurveillanceActive(false);
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setDetectedFaces([]);
  };

  // Cleanup WebSocket on unmount
  useEffect(() => {
    return () => stopSurveillance();
  },[]);

  // DRAW GREEN/RED BOXES ON CANVAS
  useEffect(() => {
    if (canvasRef.current && webcamRef.current && webcamRef.current.video) {
      const video = webcamRef.current.video;
      const canvas = canvasRef.current;
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      detectedFaces.forEach(face => {
        const[x, y, w, h] = face.box;
        ctx.beginPath();
        ctx.lineWidth = 4;
        if (face.status === 'known') { ctx.strokeStyle = '#28a745'; ctx.fillStyle = '#28a745'; } 
        else { ctx.strokeStyle = '#dc3545'; ctx.fillStyle = '#dc3545'; }
        ctx.rect(x, y, w, h);
        ctx.stroke();

        ctx.font = 'bold 18px Arial';
        const text = face.name;
        const textWidth = ctx.measureText(text).width;
        ctx.fillRect(x, y - 30, textWidth + 20, 30);
        ctx.fillStyle = '#fff';
        ctx.fillText(text, x + 10, y - 8);
      });
    }
  }, [detectedFaces]);

  const handleCanvasClick = (e) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    const clickX = (e.clientX - rect.left) * scaleX;
    const clickY = (e.clientY - rect.top) * scaleY;

    detectedFaces.forEach(face => {
      const[x, y, w, h] = face.box;
      if (face.status === 'unknown' && clickX >= x && clickX <= x + w && clickY >= y && clickY <= y + h) {
        const frame = webcamRef.current.getScreenshot();
        setQuickEnrollData({ image: frame, box: face.box });
      }
    });
  };

  const assignQuickEnroll = async (studentId, studentName) => {
    try {
      const response = await fetch(`${API_BASE}/quick-enroll`, {
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
      
      alert(`✅ ${result.message}`);
      setQuickEnrollData(null); 
    } catch (error) {
      alert(`❌ Quick Enroll Error: ${error.message}`);
    }
  };

  const closeModal = () => setIsModalOpen(false);

  return (
    <div>
      {/* ---------------- TOP BAR & NAVBAR ---------------- */}
      <div className="top-bar">
        <div>✉ info@lu.ac.ae &nbsp;&nbsp; 📞 600 500606</div>
        <div className="top-bar-right"><span>Our Campuses</span> <span>LU Connect</span> <span>Library Portal</span><button>Enquire Now</button></div>
      </div>
      <nav className="main-nav">
        <div className="logo">🛡️ Liwa <span>University</span></div>
        <div className="nav-links"><a>Home</a><a>Study</a><a>Admissions</a><a>Research</a><a>Student Life</a><a>About Us</a></div>
      </nav>

      {/* ---------------- LOGIN VIEW ---------------- */}
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

      {/* ---------------- STUDENT LIST & LIVE SURVEILLANCE ---------------- */}
      {view === 'students' && (
        <div className="app-container">
          <div className="content-box">
            <button className="back-btn" onClick={() => setView('classes')}>← Back to Classes</button>
            <h2 className="section-title" style={{display: 'block'}}>Class Roster: {selectedClass}</h2>
            {/* NEW: Title and Download Button side by side */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
              <h2 className="section-title" style={{ display: 'inline-block', margin: 0 }}>Class Roster: {selectedClass}</h2>
              <button 
                style={{ background: '#28a745', color: 'white', padding: '10px 20px', borderRadius: '5px', border: 'none', cursor: 'pointer', fontWeight: 'bold' }}
                onClick={downloadAttendanceReport}
              >
                📥 Download Excel Report
              </button>
            </div>
            
            {/* --- LIVE SURVEILLANCE UI --- */}
            <div style={{background: '#f8f9fa', padding: '20px', borderRadius: '8px', border: '2px solid #e9ecef', marginBottom: '30px', textAlign: 'center'}}>
              <h3 style={{marginTop: 0, color: 'var(--primary-dark)'}}>🎥 Live Classroom Surveillance</h3>
              <p style={{color: '#666', fontSize: '14px', marginBottom: '20px'}}>YOLOv8 + PyTorch crowd tracking. <b>Click on Red (Unknown) boxes</b> to Quick Enroll a student!</p>
              
              <div style={{position: 'relative', display: 'inline-block', width: '100%', maxWidth: '800px', margin: '0 auto'}}>
                <Webcam 
                  ref={webcamRef} 
                  audio={false} 
                  screenshotFormat="image/jpeg" 
                  style={{ width: '100%', height: 'auto', display: 'block', borderRadius: '8px', border: '3px solid #ccc' }} 
                  videoConstraints={{ facingMode: "user" }}
                />
                <canvas 
                  ref={canvasRef} 
                  onClick={handleCanvasClick}
                  style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 10, cursor: 'crosshair' }} 
                />
              </div>

              <div style={{marginTop: '20px'}}>
                <button 
                  style={{background: isSurveillanceActive ? '#dc3545' : '#2f3254', color: 'white', padding: '12px 30px', borderRadius: '30px', border: 'none', cursor: 'pointer', fontWeight: 'bold', fontSize: '16px', boxShadow: '0 4px 10px rgba(0,0,0,0.1)'}}
                  onClick={toggleSurveillance}
                >
                  {isSurveillanceActive ? '🛑 Stop Surveillance' : '▶ Start Live Attendance'}
                </button>
              </div>
            </div>

            {/* --- STUDENT LIST --- */}
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
                            <span style={{ color: 'white', background: '#28a745', padding: '6px 16px', borderRadius: '4px', fontWeight: 'bold', display: 'inline-block', width: '120px' }}>
                              ✅ Present
                            </span>
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

      {/* ---------------- FOOTER ---------------- */}
      <footer className="site-footer">
        <div className="footer-grid">
          <div><h3 style={{borderBottom: '2px solid #ffcb05', display: 'inline-block', paddingBottom: '5px'}}>🛡️ Liwa University</h3><p style={{color: '#ccc', lineHeight: '1.6'}}>Abu Dhabi Campus<br/>PO Box 41009, Abu Dhabi, UAE</p></div>
          <div><h3>Contact us</h3><ul style={{color: '#ccc'}}><li>Call Center: 600 500606</li><li>E-mail: info@lu.ac.ae</li></ul></div>
        </div>
      </footer>

      {/* ---------------- ENROLLMENT MODAL (9-Image Burst) ---------------- */}
      {isModalOpen && (
        <div className="modal-overlay">
          <div className="modal-content">
            <h2 className="modal-header">Biometric Enrollment</h2>
            {(enrollStep === 'front' || enrollStep === 'left' || enrollStep === 'right') && (
              <>
                <p>Please align the student's face within the oval.</p>
                <div className="webcam-container">
                  <Webcam audio={false} ref={webcamRef} screenshotFormat="image/jpeg" width="100%" videoConstraints={{ facingMode: "user" }} />
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

      {/* ---------------- NEW: LIVE CLICK "QUICK ENROLL" MODAL ---------------- */}
      {quickEnrollData && (
        <div className="modal-overlay">
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
            <br/>
            <button className="btn-cancel" onClick={() => setQuickEnrollData(null)}>Cancel</button>
          </div>
        </div>
      )}

      {/* ---------------- 1-ON-1 VERIFICATION MODAL ---------------- */}
      {isVerifyModalOpen && verifyingStudent && (
        <div className="modal-overlay">
          <div className="modal-content">
            <h2 className="modal-header">Verify Identity</h2>
            <h3 style={{marginTop: 0, color: '#555'}}>Target Student: {verifyingStudent['Student Name']}</h3>
            <div className="webcam-container" style={{minHeight: '250px'}}>
              <Webcam audio={false} ref={webcamRef} screenshotFormat="image/jpeg" width="100%" videoConstraints={{ facingMode: "user" }} />
              <div className="webcam-mask" style={{width: '180px', height: '240px'}}></div>
            </div>
            <div style={{margin: '15px 0', fontSize: '18px', fontWeight: 'bold', color: verifyResult.includes('❌') ? '#dc3545' : '#28a745'}}>
              {verifyResult}
            </div>
            <button className="btn-capture" onClick={runVerificationScan}>🔍 Scan Face</button>
            <button className="btn-cancel" onClick={() => setIsVerifyModalOpen(false)}>Close</button>
          </div>
        </div>
      )}

    </div>
  );
}

export default App;