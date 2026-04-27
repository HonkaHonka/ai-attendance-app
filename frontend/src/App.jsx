import React, { useState, useRef, useEffect } from 'react';
import Webcam from "react-webcam";
import './App.css';

const API_BASE = "http://127.0.0.1:8000/api";
const WS_BASE = "ws://127.0.0.1:8000/ws";

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
  const[capturedImages, setCapturedImages] = useState({});
  const[isCapturing, setIsCapturing] = useState(false); 
  
  const[isVerifyModalOpen, setIsVerifyModalOpen] = useState(false);
  const[verifyResult, setVerifyResult] = useState('');
  const[verifyingStudent, setVerifyingStudent] = useState(null); 
  const[attendanceRecords, setAttendanceRecords] = useState({}); 

  const[isSurveillanceActive, setIsSurveillanceActive] = useState(false);
  const[detectedFaces, setDetectedFaces] = useState([]);
  
  const [liveZoom, setLiveZoom] = useState(null); 
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
      if (!res.ok) throw new Error("Faculty email not found.");
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
  };

  useEffect(() => { return () => stopSurveillance(); },[]);

  // 🚀 THE FIX: PERFECT LETTERBOX GEOMETRY 
  useEffect(() => {
    if (canvasRef.current && surveillanceWebcamRef.current && surveillanceWebcamRef.current.video) {
      const video = surveillanceWebcamRef.current.video;
      const canvas = canvasRef.current;
      
      if (video.videoWidth > 0 && video.videoHeight > 0) {
        canvas.width = video.clientWidth;
        canvas.height = video.clientHeight;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Calculate invisible "Black Bar" offsets
        const vRatio = video.videoWidth / video.videoHeight;
        const dRatio = canvas.width / canvas.height;
        let renderW = canvas.width;
        let renderH = canvas.height;
        let offsetX = 0;
        let offsetY = 0;
        
        if (vRatio > dRatio) {
          renderH = canvas.width / vRatio;
          offsetY = (canvas.height - renderH) / 2;
        } else {
          renderW = canvas.height * vRatio;
          offsetX = (canvas.width - renderW) / 2;
        }
        
        const scale = renderW / video.videoWidth;

        detectedFaces.forEach(face => {
          const[origX, origY, origW, origH] = face.box;
          
          // Apply exact mathematical offset to stick perfectly to the body!
          const x = (origX * scale) + offsetX;
          const y = (origY * scale) + offsetY;
          const w = origW * scale;
          const h = origH * scale;

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
  },[detectedFaces]);

  // 🚀 THE FIX: PERFECT CLICK MAPPING
  const handleCanvasClick = (e) => {
    const canvas = canvasRef.current;
    if (!canvas || !surveillanceWebcamRef.current || !surveillanceWebcamRef.current.video) return;
    
    const rect = canvas.getBoundingClientRect();
    const video = surveillanceWebcamRef.current.video;
    
    // Same math to reverse-engineer the click coordinates
    const vRatio = video.videoWidth / video.videoHeight;
    const dRatio = rect.width / rect.height;
    let renderW = rect.width;
    let renderH = rect.height;
    let offsetX = 0;
    let offsetY = 0;
    
    if (vRatio > dRatio) {
      renderH = rect.width / vRatio;
      offsetY = (rect.height - renderH) / 2;
    } else {
      renderW = rect.height * vRatio;
      offsetX = (rect.width - renderW) / 2;
    }
    const scale = renderW / video.videoWidth;
    
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    detectedFaces.forEach(face => {
      const[origX, origY, origW, origH] = face.box;
      
      const x = (origX * scale) + offsetX;
      const y = (origY * scale) + offsetY;
      const w = origW * scale;
      const h = origH * scale;

      // Allow clicking unknown OR scanning!
      if ((face.status === 'unknown' || face.status === 'scanning') && 
          clickX >= x && clickX <= x + w && 
          clickY >= y && clickY <= y + h) {
        
        const frame = surveillanceWebcamRef.current.getScreenshot();
        
        // 🚀 FIX: Target lock the Face (Top 20% of the body), NOT the stomach!
        setLiveZoom({
           origBox: face.box,
           centerX: x + (w / 2),
           centerY: y + (h * 0.20) 
        });
        
        setQuickEnrollData({ image: frame, box: face.box });
        stopSurveillance(); 
      }
    });
  };

  const assignLiveEnroll = async (studentId, studentName) => {
    try {
      const[x, y, w, h] = liveZoom.origBox;
      const pad = w * 0.3;
      const expandedBox =[ Math.max(0, x - pad), Math.max(0, y - pad), w + (pad * 2), h + (pad * 2) ];

      const response = await fetch(`${API_BASE}/assign-face`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            student_id: String(studentId),
            student_name: studentName,
            image: quickEnrollData.image,
            box: expandedBox 
          })
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail);
      
      alert(`✅ Successfully Enrolled ${studentName}!`);
      setLiveZoom(null); 
      setQuickEnrollData(null);
      toggleSurveillance(); 
      
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
        <div style={{ position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh', background: '#000', zIndex: 3000, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          
          <div style={{ padding: '15px 30px', background: '#111', color: 'white', display: 'flex', justifyContent: 'space-between', alignItems: 'center', zIndex: 3010 }}>
            <h2 style={{ margin: 0, color: 'var(--accent-gold)' }}>🔴 Live Classroom Tracking</h2>
            <button onClick={stopSurveillance} style={{ background: '#dc3545', color: 'white', border: 'none', padding: '10px 25px', borderRadius: '5px', fontWeight: 'bold', cursor: 'pointer' }}>
              Close Tracker
            </button>
          </div>

          <div style={{ flex: 1, position: 'relative', background: '#000', overflow: 'hidden' }}>
            
            <div style={{ 
              position: 'absolute', top: 0, left: 0, width: '100%', height: '100%',
              transform: liveZoom ? 'scale(2.5)' : 'scale(1)',
              transformOrigin: liveZoom ? `${liveZoom.centerX}px ${liveZoom.centerY}px` : 'center center',
              transition: 'transform 0.4s ease-out'
            }}>
              <Webcam ref={surveillanceWebcamRef} audio={false} mirrored={false} screenshotFormat="image/jpeg" style={{ width: '100%', height: '100%', display: 'block', objectFit: 'contain', position: 'absolute' }} />
              <canvas ref={canvasRef} onClick={handleCanvasClick} style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 10, cursor: 'crosshair' }} />
            </div>

            {liveZoom && (
              <div style={{ position: 'absolute', right: 0, top: 0, width: '400px', height: '100%', background: 'rgba(20,20,30,0.95)', zIndex: 4000, padding: '20px', borderLeft: '4px solid var(--accent-gold)', overflowY: 'auto', boxShadow: '-10px 0 30px rgba(0,0,0,0.5)' }}>
                <h2 style={{color: 'white', marginTop: 0}}>⚡ Live Enroll</h2>
                <p style={{color: '#aaa', fontSize: '14px'}}>Target locked. Click their name below to instantly capture their DNA.</p>
                <button className="btn-cancel" style={{width: '100%', marginBottom: '20px'}} onClick={() => { setLiveZoom(null); setQuickEnrollData(null); toggleSurveillance(); }}>
                  Cancel & Zoom Out
                </button>

                <div className="student-select-list" style={{border: '1px solid #444', borderRadius: '8px', background: '#2a2a3c'}}>
                  {students.map((student, idx) => (
                    <div key={idx} className="student-select-item" style={{borderBottomColor: '#444', color: 'white'}} onClick={() => assignLiveEnroll(student['Student ID'], student['Student Name'])}>
                      <span><b>{student['Student ID']}</b> - {student['Student Name']}</span><span style={{color: 'var(--accent-gold)'}}>Assign ➔</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  );
}

export default App;