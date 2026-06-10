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
  const [isRosterVisible, setIsRosterVisible] = useState(true);
  const [isDashboardOpen, setIsDashboardOpen] = useState(false);
  const [dbHealth, setDbHealth] = useState(null);
  
  // REFS
  const webcamRef = useRef(null);
  const canvasRef = useRef(null);
  const wsRef = useRef(null);
  const surveillanceWebcamRef = useRef(null); 
  const inspectCanvasRef = useRef(null); 
  const frameIntervalRef = useRef(null);
  const waitingForResponse = useRef(false);
  
  


  const getCanvasContentBounds = (canvas) => {
    const rect = canvas.getBoundingClientRect();
    const canvasRatio = AI_W / AI_H;
    const rectRatio = rect.width / rect.height;

    let contentWidth, contentHeight, offsetX, offsetY;

    if (rectRatio > canvasRatio) {
      contentHeight = rect.height;
      contentWidth = contentHeight * canvasRatio;
      offsetX = (rect.width - contentWidth) / 2;
      offsetY = 0;
    } else {
      contentWidth = rect.width;
      contentHeight = contentWidth / canvasRatio;
      offsetX = 0;
      offsetY = (rect.height - contentHeight) / 2;
    }
    return { contentWidth, contentHeight, offsetX, offsetY, rect };
  };

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
    // ==========================================
  // WEBSOCKET & SURVEILLANCE — ACK-BASED PACING
  // ==========================================
      // ==========================================
  // WEBSOCKET & SURVEILLANCE — RELIABLE INTERVAL + ACK GUARD
  // ==========================================
  const sendFrameToWebSocket = () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && !waitingForResponse.current) {
      const video = surveillanceWebcamRef.current?.video;
      // 🎯 Use original check: videoWidth > 0 (readyState >= 2 blocks too early)
      if (video && video.videoWidth > 0) {
        const tempCanvas = document.createElement('canvas');
        tempCanvas.width = AI_W;
        tempCanvas.height = AI_H;
        const ctx = tempCanvas.getContext('2d');
        ctx.drawImage(video, 0, 0, AI_W, AI_H);
        const imageSrc = tempCanvas.toDataURL('image/jpeg', 0.6);
        
        waitingForResponse.current = true;
        wsRef.current.send(JSON.stringify({ image: imageSrc }));
      }
    }
  };

  const toggleSurveillance = () => {
    if (isSurveillanceActive) {
      stopSurveillance();
    } else {
      setIsSurveillanceActive(true);
      waitingForResponse.current = false;
      
      wsRef.current = new WebSocket(`${WS_BASE}/surveillance`);
      
      wsRef.current.onopen = () => {
        // 🎯 Old reliable interval, but guarded by waitingForResponse
        frameIntervalRef.current = setInterval(sendFrameToWebSocket, 500);
      };
      
      wsRef.current.onmessage = (event) => {
        waitingForResponse.current = false;
        
        const data = JSON.parse(event.data);
        if (data.faces) {
          setDetectedFaces(data.faces);
          const newRecords = {};
          data.faces.forEach(face => {
            if (face.status === 'known' && face.student_id) {
              newRecords[face.student_id] = 'present';
            }
          });
          setAttendanceRecords(prev => ({ ...prev, ...newRecords }));
        }
      };
      
      wsRef.current.onerror = () => { stopSurveillance(); };
    }
  };

  const stopSurveillance = () => {
    setIsSurveillanceActive(false);
    if (frameIntervalRef.current) {
      clearInterval(frameIntervalRef.current);
      frameIntervalRef.current = null;
    }
    if (wsRef.current) { 
      wsRef.current.close(); 
      wsRef.current = null; 
    }
    waitingForResponse.current = false;
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
    // ==========================================
  // CANVAS OVERLAY DRAWING — HEAD LEVEL ONLY
  // ==========================================
    // ==========================================
  // CANVAS OVERLAY — NAME TAGS ONLY (NO BODY BOXES)
  // ==========================================
  useEffect(() => {
    if (canvasRef.current && detectedFaces) {
      const canvas = canvasRef.current;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, AI_W, AI_H);

      detectedFaces.forEach(face => {
        const [x, y, w, h] = face.box; // YOLO body box — accurate tracking anchor

        // Determine text and color based on status
        let text, bgColor;
        if (face.status === 'known') {
          text = face.name;
          bgColor = 'rgba(40, 167, 69, 0.95)';
        } else if (face.status === 'scanning') {
          text = 'Scanning...';
          bgColor = 'rgba(255, 203, 5, 0.95)';
        } else if (face.status === 'occluded') {
          text = 'Occluded';
          bgColor = 'rgba(255, 152, 0, 0.95)';
        } else {
          text = 'Unknown';
          bgColor = 'rgba(220, 53, 69, 0.95)';
        }

        // Measure text for pill background
        ctx.font = 'bold 18px Arial';
        const textWidth = ctx.measureText(text).width;
        const padding = 12;
        const tagWidth = Math.max(textWidth + padding * 2, 80);
        const tagHeight = 30;

        // Position: centered above the body box
        const tagX = x + w / 2 - tagWidth / 2;
        const tagY = y - tagHeight - 6;

        // Draw pill background
        ctx.fillStyle = bgColor;
        if (ctx.roundRect) {
          ctx.beginPath();
          ctx.roundRect(tagX, tagY, tagWidth, tagHeight, 6);
          ctx.fill();
        } else {
          ctx.fillRect(tagX, tagY, tagWidth, tagHeight);
        }

        // Draw text
        ctx.fillStyle = '#fff';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(text, x + w / 2, tagY + tagHeight / 2 + 1);
        ctx.textAlign = 'left';
        ctx.textBaseline = 'alphabetic';

        // Tiny lock-on dot at upper body (visual anchor only)
        ctx.fillStyle = face.status === 'known' ? '#28a745' : '#dc3545';
        ctx.beginPath();
        ctx.arc(x + w / 2, y + h * 0.25, 4, 0, 2 * Math.PI);
        ctx.fill();
      });
    }
  }, [detectedFaces]);

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

    const { contentWidth, contentHeight, offsetX, offsetY, rect } = getCanvasContentBounds(canvas);

    const clickX_display = e.clientX - rect.left - offsetX;
    const clickY_display = e.clientY - rect.top - offsetY;

    // Ignore clicks on black letterbox bars
    if (clickX_display < 0 || clickX_display > contentWidth || clickY_display < 0 || clickY_display > contentHeight) {
      return;
    }

    const clickX = clickX_display * (AI_W / contentWidth);
    const clickY = clickY_display * (AI_H / contentHeight);

    detectedFaces.forEach(face => {
      const [x, y, w, h] = face.box;

      // Expand hit area upward to include the name tag (so clicking "Unknown" text works)
      const hitY = y - 40;
      const hitH = h + 40;

      if (clickX >= x && clickX <= x + w && clickY >= hitY && clickY <= hitY + hitH) {
        
        // 🟢 KNOWN: Click to unassign (no update button needed)
        if (face.status === 'known' && face.student_id) {
          if (window.confirm(`Unassign ${face.name}?\n\nThis removes their biometric data so you can reassign correctly. Other students stay untouched.`)) {
            unassignStudent(face.student_id, face.name);
          }
          return;
        }

        // 🔴 UNKNOWN / SCANNING: Click to assign
        if (face.status === 'unknown' || face.status === 'scanning' || face.status === 'no_face') {
          const video = surveillanceWebcamRef.current?.video;
          if (!video || video.readyState < 2) return;

          const tempCanvas = document.createElement('canvas');
          tempCanvas.width = AI_W;
          tempCanvas.height = AI_H;
          tempCanvas.getContext('2d').drawImage(video, 0, 0, AI_W, AI_H);

          setQuickEnrollData({
            image: tempCanvas.toDataURL('image/jpeg', 0.8),
            box: face.box,
            isManual: false
          });
          setLiveZoom({ origBox: face.box, isManual: false });
        }
      }
    });
  };

    const handleCanvasRightClick = (e) => {
    e.preventDefault();

    const canvas = canvasRef.current;
    if (!canvas) return;

    const { contentWidth, contentHeight, offsetX, offsetY, rect } = getCanvasContentBounds(canvas);

    const clickX_display = e.clientX - rect.left - offsetX;
    const clickY_display = e.clientY - rect.top - offsetY;

    if (clickX_display < 0 || clickX_display > contentWidth || clickY_display < 0 || clickY_display > contentHeight) {
      return;
    }

    const clickX_AI = clickX_display * (AI_W / contentWidth);
    const clickY_AI = clickY_display * (AI_H / contentHeight);

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

    const { contentWidth, contentHeight, offsetX, offsetY, rect } = getCanvasContentBounds(canvas);

    const mouseX_display = e.clientX - rect.left - offsetX;
    const mouseY_display = e.clientY - rect.top - offsetY;

    if (mouseX_display < 0 || mouseX_display > contentWidth || mouseY_display < 0 || mouseY_display > contentHeight) {
      return;
    }

    const mouseX = mouseX_display * (AI_W / contentWidth);
    const mouseY = mouseY_display * (AI_H / contentHeight);

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
        if (ratio < 0.003) zoomScale = 5.0;
        else if (ratio < 0.008) zoomScale = 4.0;
        else if (ratio < 0.02) zoomScale = 3.0;
        else if (ratio < 0.05) zoomScale = 2.0;
        else zoomScale = 1.3;

        setWheelZoom({
          centerX: mouseX,
          centerY: mouseY,
          scale: zoomScale,
          trackId: targetFace.track_id,
          faceBox: null
        });
        setInspectMode(null);
      } else {
        setWheelZoom({
          centerX: mouseX,
          centerY: mouseY,
          scale: 3.5,
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
        let errMsg;
        if (response.status === 422) {
          errMsg = result.detail?.map(d => `${d.loc?.join('.')}: ${d.msg}`).join('; ') 
                   || JSON.stringify(result.detail);
        } else {
          errMsg = result.detail || result.message || `Server error: ${response.status}`;
        }
        throw new Error(errMsg);
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

  const unassignStudent = async (studentId, studentName) => {
    if (!window.confirm(`Unassign ${studentName}?\n\nThis will remove their biometric data and mark them absent. Other students stay untouched.`)) {
      return;
    }
    try {
      const response = await fetch(`${API_BASE}/unassign-student`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ student_id: String(studentId) })
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Unassign failed');
      
      // Remove from local attendance records
      setAttendanceRecords(prev => {
        const next = { ...prev };
        delete next[studentId];
        return next;
      });
      
      alert(`✅ ${result.message}`);
      setIsUpdateMode(false);
    } catch (error) {
      alert(`❌ Error: ${error.message}`);
    }
  };
  
  const fetchDbHealth = async () => {
    try {
      const res = await fetch(`${API_BASE}/db-health`);
      if (!res.ok) throw new Error("Failed to fetch health");
      setDbHealth(await res.json());
      setIsDashboardOpen(true);
    } catch (err) {
      alert(`⚠️ Health check failed: ${err.message}`);
    }
  };
    // ==========================================
  // CAMERA FOCUS STATE (Adapts to smart tracking)
  // ==========================================
  const cameraFocus = React.useMemo(() => {
    if (!detectedFaces || detectedFaces.length === 0) {
      return { mode: 'empty', target: null, coverage: 0 };
    }
    
    const frameArea = AI_W * AI_H;
    const totalBoxArea = detectedFaces.reduce((sum, f) => sum + f.box[2] * f.box[3], 0);
    const coverage = totalBoxArea / frameArea;
    
    const sorted = [...detectedFaces].sort((a, b) => (b.box[2]*b.box[3]) - (a.box[2]*a.box[3]));
    const largest = sorted[0];
    const largestRatio = (largest.box[2] * largest.box[3]) / frameArea;
    
    if (detectedFaces.length === 1 && largestRatio > 0.20) {
      return { mode: 'focused', target: largest, coverage };
    }
    if (detectedFaces.length === 2 && coverage > 0.45 && largestRatio > 0.18) {
      return { mode: 'focused', target: largest, coverage };
    }
    if (coverage < 0.15 && detectedFaces.length >= 3) {
      return { mode: 'wide', target: null, coverage };
    }
    if (detectedFaces.length >= 2 && coverage > 0.60) {
      return { mode: 'crowded', target: null, coverage };
    }
    
    return { mode: 'normal', target: null, coverage };
  }, [detectedFaces]);
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
          
           <div style={{ 
            padding: '12px 20px', 
            background: '#111', 
            color: 'white', 
            display: 'flex', 
            justifyContent: 'space-between', 
            alignItems: 'center', 
            zIndex: 3010,
            minHeight: '50px',
            flexWrap: 'wrap',
            gap: '10px'
          }}>
            <h2 style={{ margin: 0, color: '#ffcb05', fontSize: 'clamp(16px, 2vw, 22px)', whiteSpace: 'nowrap' }}>
              🔴 Live Classroom Tracking
            </h2>
            <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexShrink: 0 }}>
              
              <button onClick={fetchDbHealth} style={{ 
                background: '#2f3254', 
                color: '#ffcb05', 
                border: '2px solid #ffcb05', 
                padding: '8px 16px', 
                borderRadius: '5px', 
                fontWeight: 'bold', 
                cursor: 'pointer',
                fontSize: 'clamp(12px, 1.2vw, 14px)',
                whiteSpace: 'nowrap',
                flexShrink: 0
              }}>
                📊 DB Health
              </button>
              <button onClick={stopSurveillance} style={{ 
                background: '#dc3545', 
                color: 'white', 
                border: 'none', 
                padding: '8px 16px', 
                borderRadius: '5px', 
                fontWeight: 'bold', 
                cursor: 'pointer',
                fontSize: 'clamp(12px, 1.2vw, 14px)',
                whiteSpace: 'nowrap',
                flexShrink: 0
              }}>
                Close Tracker
              </button>
            </div>
          </div>
                    {/* 🎯 CAMERA FOCUS INDICATOR */}
          <div style={{ 
            position: 'absolute', 
            top: '70px', 
            left: '50%', 
            transform: 'translateX(-50%)', 
            zIndex: 3010,
            background: cameraFocus.mode === 'focused' ? 'rgba(40, 167, 69, 0.9)' : 
                         cameraFocus.mode === 'wide' ? 'rgba(47, 50, 84, 0.9)' : 
                         cameraFocus.mode === 'crowded' ? 'rgba(255, 193, 7, 0.9)' : 'rgba(108, 117, 125, 0.9)',
            color: 'white',
            padding: '10px 24px',
            borderRadius: '20px',
            fontSize: '15px',
            fontWeight: 'bold',
            pointerEvents: 'none',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
            transition: 'all 0.3s ease'
          }}>
            {cameraFocus.mode === 'focused' && cameraFocus.target?.status === 'unknown' && (
              <span>🎯 CAMERA FOCUSED — Click the large red box to assign</span>
            )}
            {cameraFocus.mode === 'focused' && cameraFocus.target?.status === 'known' && (
              <span>✅ FOCUSED ON {cameraFocus.target.name} — Already marked present</span>
            )}
            {cameraFocus.mode === 'wide' && (
              <span>📷 WIDE VIEW — Call names; camera will auto-focus on responders</span>
            )}
            {cameraFocus.mode === 'crowded' && (
              <span>⚠️ MULTIPLE MOVEMENTS — Wait for camera to settle on one person</span>
            )}
            {cameraFocus.mode === 'normal' && (
              <span>🔍 SCANNING — Use left-click or wheel zoom to inspect</span>
            )}
            {cameraFocus.mode === 'empty' && (
              <span>📷 NO BODIES DETECTED — Check camera position</span>
            )}
          </div>

          

                    {/* 📊 DB HEALTH DASHBOARD — RIGHT SIDE PANEL */}
          {isDashboardOpen && dbHealth && (
            <div style={{
              position: 'absolute',
              top: '70px',
              right: '10px',
              width: 'clamp(320px, 28vw, 420px)',
              height: 'calc(100% - 80px)',
              background: 'rgba(15, 15, 25, 0.98)',
              border: '3px solid #ffcb05',
              borderRadius: '12px',
              zIndex: 4500,
              padding: '20px',
              overflowY: 'auto',
              boxShadow: '0 20px 60px rgba(0,0,0,0.9)',
              display: 'flex',
              flexDirection: 'column'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: '2px solid #ffcb05', paddingBottom: '12px' }}>
                <h3 style={{ margin: 0, color: '#ffcb05', fontSize: '16px' }}>📊 DB Health</h3>
                <button onClick={() => setIsDashboardOpen(false)} style={{ background: '#dc3545', color: 'white', border: 'none', padding: '6px 12px', borderRadius: '4px', fontWeight: 'bold', cursor: 'pointer', fontSize: '12px' }}>
                  Close
                </button>
              </div>
              
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '15px' }}>
                <div style={{ background: '#2a2a3c', padding: '10px', borderRadius: '6px', textAlign: 'center' }}>
                  <div style={{ fontSize: '22px', fontWeight: 'bold', color: 'white' }}>{dbHealth.total_students}</div>
                  <div style={{ color: '#aaa', fontSize: '11px' }}>Students</div>
                </div>
                <div style={{ background: '#2a2a3c', padding: '10px', borderRadius: '6px', textAlign: 'center' }}>
                  <div style={{ fontSize: '22px', fontWeight: 'bold', color: dbHealth.suspicious_count > 0 ? '#dc3545' : '#28a745' }}>{dbHealth.suspicious_count}</div>
                  <div style={{ color: '#aaa', fontSize: '11px' }}>Suspicious</div>
                </div>
              </div>
              
              <div style={{ flex: 1, overflowY: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                  <thead>
                    <tr style={{ background: '#2f3254', color: '#ffcb05' }}>
                      <th style={{ padding: '8px', textAlign: 'left' }}>Name</th>
                      <th style={{ padding: '8px', textAlign: 'center' }}>Emb</th>
                      <th style={{ padding: '8px', textAlign: 'center' }}>Min</th>
                      <th style={{ padding: '8px', textAlign: 'center' }}>Flag</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dbHealth.students.map((s, idx) => (
                      <tr key={idx} style={{ borderBottom: '1px solid #444', background: s.flag !== 'OK' ? 'rgba(220, 53, 69, 0.1)' : 'transparent' }}>
                        <td style={{ padding: '8px', color: 'white' }}>
                          <div style={{ fontWeight: 'bold' }}>{s.name}</div>
                          <div style={{ color: '#888', fontSize: '10px' }}>{s.student_id}</div>
                        </td>
                        <td style={{ padding: '8px', textAlign: 'center', color: 'white' }}>{s.embedding_count}</td>
                        <td style={{ padding: '8px', textAlign: 'center', color: s.min_self_similarity < 0.75 ? '#dc3545' : '#28a745' }}>{s.min_self_similarity}</td>
                        <td style={{ padding: '8px', textAlign: 'center' }}>
                          <span style={{
                            padding: '2px 6px', borderRadius: '3px', fontSize: '10px', fontWeight: 'bold',
                            background: s.flag === 'OK' ? '#28a745' : s.flag === 'HIGH_VARIANCE' ? '#dc3545' : '#ffcb05',
                            color: 'white'
                          }}>
                            {s.flag}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          
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
                                    {/* 📋 ROSTER TOGGLE BUTTON */}
            <button
              onClick={() => setIsRosterVisible(!isRosterVisible)}
              style={{
                position: 'absolute',
                left: isRosterVisible ? '360px' : '0',
                top: '50%',
                transform: 'translateY(-50%)',
                zIndex: 3600,
                background: '#ffcb05',
                color: '#1a1a2e',
                border: 'none',
                padding: '14px 10px',
                borderRadius: '0 8px 8px 0',
                cursor: 'pointer',
                fontWeight: 'bold',
                fontSize: '18px',
                boxShadow: '2px 0 10px rgba(0,0,0,0.4)',
                transition: 'left 0.3s ease',
                writingMode: 'vertical-rl',
                textOrientation: 'mixed'
              }}
            >
              {isRosterVisible ? '← Hide' : '📋 Show'}
            </button>

            {/* 📋 LEFT PANEL — Collapsible Student Roster */}
            {isRosterVisible && (
              <div style={{
                position: 'absolute',
                left: 0,
                top: 0,
                width: '360px',
                height: '100%',
                background: 'rgba(15, 15, 25, 0.95)',
                zIndex: 3400,
                padding: '20px',
                borderRight: '3px solid #ffcb05',
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden'
              }}>
                <h3 style={{ color: '#ffcb05', margin: '0 0 10px 0', fontSize: '18px', fontWeight: 'bold' }}>
                  📋 Class Roster
                </h3>
                <div style={{ color: '#aaa', fontSize: '13px', marginBottom: '15px', borderBottom: '1px solid #444', paddingBottom: '10px' }}>
                  <div style={{ color: 'white', fontWeight: 'bold', fontSize: '14px', marginBottom: '4px' }}>
                    {selectedClass} — {students.length} Students
                  </div>
                  <div>Call names from the list. Click a face to assign.</div>
                </div>
                
                <div style={{ flex: 1, overflowY: 'auto', paddingRight: '6px' }}>
                  {students.map((student, idx) => {
                    const status = attendanceRecords[student['Student ID']];
                    const isPresent = status === 'present';
                    const isFailed = status === 'failed';
                    return (
                      <div key={idx} style={{
                        padding: '10px 12px',
                        marginBottom: '8px',
                        borderRadius: '6px',
                        background: isPresent ? 'rgba(40, 167, 69, 0.15)' : isFailed ? 'rgba(220, 53, 69, 0.15)' : 'rgba(255,255,255,0.04)',
                        border: `1px solid ${isPresent ? '#28a745' : isFailed ? '#dc3545' : '#444'}`,
                        transition: 'all 0.2s'
                      }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                          <span style={{ color: 'white', fontWeight: 'bold', fontSize: '14px' }}>
                            {student['Student Name']}
                          </span>
                          <span style={{
                            padding: '3px 8px',
                            borderRadius: '4px',
                            fontSize: '11px',
                            fontWeight: 'bold',
                            background: isPresent ? '#28a745' : isFailed ? '#dc3545' : '#666',
                            color: 'white'
                          }}>
                            {isPresent ? '✅' : isFailed ? '❌' : '⏳'}
                          </span>
                        </div>
                        <div style={{ color: '#888', fontSize: '12px' }}>
                          ID: {student['Student ID']}
                        </div>
                      </div>
                    );
                  })}
                </div>
                
                <div style={{ marginTop: '15px', paddingTop: '12px', borderTop: '2px solid #ffcb05' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', color: 'white', fontSize: '14px', fontWeight: 'bold', marginBottom: '6px' }}>
                    <span>Marked Present:</span>
                    <span style={{ color: '#28a745' }}>
                      {students.filter(s => attendanceRecords[s['Student ID']] === 'present').length} / {students.length}
                    </span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', color: '#aaa', fontSize: '12px' }}>
                    <span>DB Students:</span>
                    <span>{Object.keys(detectedFaces.reduce((acc,f) => { if(f.status==='known' && f.student_id) acc[f.student_id]=true; return acc; }, {})).length} enrolled</span>
                  </div>
                </div>
              </div>
            )}
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