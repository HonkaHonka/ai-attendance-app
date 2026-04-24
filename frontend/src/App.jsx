import React, { useState, useRef, useEffect } from 'react';
import Webcam from "react-webcam";
import './App.css';

const API_BASE = "http://127.0.0.1:8000/api";
const WS_BASE = "ws://127.0.0.1:8000/ws";

/* ============================
   TV-OPTIMIZED CAMERA SETTINGS
============================ */
const VIDEO_CONSTRAINTS = {
  width: 1280,
  height: 720,
  facingMode: "user"
};

const CAPTURE_INTERVAL_MS = 80; // ~12.5 FPS

function App() {
  const [view, setView] = useState('login');
  const [email, setEmail] = useState('');
  const [facultyName, setFacultyName] = useState('');
  const [classes, setClasses] = useState([]);
  const [students, setStudents] = useState([]);
  const [selectedClass, setSelectedClass] = useState('');
  const [error, setError] = useState('');
  const [attendanceRecords, setAttendanceRecords] = useState({});
  const [detectedFaces, setDetectedFaces] = useState([]);
  const [isSurveillanceActive, setIsSurveillanceActive] = useState(false);

  const webcamRef = useRef(null);
  const canvasRef = useRef(null);
  const wsRef = useRef(null);
  const captureTimerRef = useRef(null);

  /* ============================
     AUTH & BASIC API CALLS
  ============================ */
  const handleLogin = async (e) => {
    e.preventDefault();
    setError('');
    try {
      const res = await fetch(`${API_BASE}/login?email=${encodeURIComponent(email)}`);
      if (!res.ok) throw new Error("Faculty email not found or server offline.");
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
    } catch {
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
    } catch {
      alert("Error loading students");
    }
  };

  /* ============================
     TV-SAFE FRAME SENDER
  ============================ */
  const sendFrameToWebSocket = () => {
    if (
      wsRef.current &&
      wsRef.current.readyState === WebSocket.OPEN &&
      webcamRef.current
    ) {
      const image = webcamRef.current.getScreenshot();
      if (image) {
        wsRef.current.send(JSON.stringify({ image }));
      }
    }
  };

  /* ============================
     SURVEILLANCE CONTROL
  ============================ */
  const toggleSurveillance = () => {
    if (isSurveillanceActive) {
      stopSurveillance();
      return;
    }

    setIsSurveillanceActive(true);
    wsRef.current = new WebSocket(`${WS_BASE}/surveillance`);

    wsRef.current.onopen = () => {
      captureTimerRef.current = setInterval(
        sendFrameToWebSocket,
        CAPTURE_INTERVAL_MS
      );
    };

    wsRef.current.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.faces) {
        setDetectedFaces(data.faces);

        const present = {};
        data.faces.forEach(face => {
          if (face.student_id) {
            present[face.student_id] = 'present';
          }
        });

        setAttendanceRecords(prev => ({ ...prev, ...present }));
      }
    };

    wsRef.current.onerror = stopSurveillance;
  };

  const stopSurveillance = () => {
    setIsSurveillanceActive(false);

    if (captureTimerRef.current) {
      clearInterval(captureTimerRef.current);
      captureTimerRef.current = null;
    }

    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    setDetectedFaces([]);
  };

  useEffect(() => () => stopSurveillance(), []);

  /* ============================
     CANVAS DRAWING
  ============================ */
  useEffect(() => {
    const canvas = canvasRef.current;
    const video = webcamRef.current?.video;
    if (!canvas || !video) return;

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    detectedFaces.forEach(face => {
      const [x, y, w, h] = face.box;
      const isUnknown = face.name === "Unknown";

      ctx.lineWidth = 3;
      ctx.strokeStyle = isUnknown ? "#dc3545" : "#28a745";
      ctx.strokeRect(x, y, w, h);

      ctx.font = "16px Arial";
      ctx.fillStyle = ctx.strokeStyle;
      ctx.fillText(face.name, x, y - 8);
    });
  }, [detectedFaces]);

  /* ============================
     UI
  ============================ */
  return (
    <div>
      {view === 'login' && (
        <form onSubmit={handleLogin}>
          <input
            type="email"
            placeholder="Faculty email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            required
          />
          {error && <p style={{ color: 'red' }}>{error}</p>}
          <button type="submit">Login</button>
        </form>
      )}

      {view === 'classes' && (
        <div>
          <h2>Welcome, {facultyName}</h2>
          {classes.map((cls, i) => (
            <button key={i} onClick={() => fetchStudents(cls['Class Nbr'])}>
              {cls['Course Name']} ({cls['Class Nbr']})
            </button>
          ))}
        </div>
      )}

      {view === 'students' && (
        <div style={{ textAlign: "center" }}>
          <Webcam
            ref={webcamRef}
            audio={false}
            screenshotFormat="image/jpeg"
            screenshotQuality={0.7}
            videoConstraints={VIDEO_CONSTRAINTS}
            style={{ width: '100%', maxWidth: '800px' }}
          />

          <canvas
            ref={canvasRef}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              pointerEvents: 'none'
            }}
          />

          <button onClick={toggleSurveillance}>
            {isSurveillanceActive ? "Stop Surveillance" : "Start Surveillance"}
          </button>

          <table>
            <thead>
              <tr>
                <th>Student ID</th>
                <th>Student Name</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {students.map((s, i) => (
                <tr key={i}>
                  <td>{s['Student ID']}</td>
                  <td>{s['Student Name']}</td>
                  <td>
                    {attendanceRecords[s['Student ID']] === 'present'
                      ? 'Present'
                      : 'Absent'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <button onClick={() => setView('classes')}>Back</button>
        </div>
      )}
    </div>
  );
}

export default App;