import React, { useState } from 'react';
import './App.css';

const API_BASE = "http://127.0.0.1:8000/api";

function App() {
  const[view, setView] = useState('login'); 
  const[email, setEmail] = useState('');
  const[facultyName, setFacultyName] = useState('');
  const[classes, setClasses] = useState([]);
  const[students, setStudents] = useState([]);
  const[selectedClass, setSelectedClass] = useState('');
  const[error, setError] = useState('');

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

  // 2. Fetch Classes
  const fetchClasses = async (userEmail) => {
    try {
      const res = await fetch(`${API_BASE}/classes?email=${encodeURIComponent(userEmail)}`);
      const data = await res.json();
      setClasses(data);
      setView('classes'); 
    } catch (err) {
      alert("Error loading classes");
    }
  };

  // 3. Fetch Students for a class
  const fetchStudents = async (classNbr) => {
    try {
      const res = await fetch(`${API_BASE}/students?email=${encodeURIComponent(email)}&class_nbr=${classNbr}`);
      const data = await res.json();
      setStudents(data);
      setSelectedClass(classNbr);
      setView('students');
    } catch (err) {
      alert("Error loading students");
    }
  };

  return (
    <div>
      {/* ---------------- TOP BAR & NAVBAR ---------------- */}
      <div className="top-bar">
        <div>✉ info@lu.ac.ae &nbsp;&nbsp; 📞 600 500606</div>
        <div className="top-bar-right">
          <span>Our Campuses</span> <span>LU Connect</span> <span>Library Portal</span>
          <button>Enquire Now</button>
        </div>
      </div>

      <nav className="main-nav">
        <div className="logo">
          🛡️ Liwa <span>University</span>
        </div>
        <div className="nav-links">
          <a>Home</a>
          <a>Study</a>
          <a>Admissions</a>
          <a>Research</a>
          <a>Student Life</a>
          <a>About Us</a>
        </div>
      </nav>

      {/* ---------------- SCROLLING LANDING PAGE (LOGIN VIEW) ---------------- */}
      {view === 'login' && (
        <>
          {/* SECTION 1: HERO & LOGIN */}
          <div className="hero-section">
            <h1 className="hero-title">Liwa University</h1>
            <h2 style={{fontWeight: 'normal', margin: 0}}>Rated for Excellence</h2>
            <div className="hero-stars">★★★★★</div>
            
            <div className="login-card">
              <h2>Faculty Portal</h2>
              <p style={{color: '#666', fontSize: '14px'}}>Sign in to view your academic schedule and manage student attendance.</p>
              <form onSubmit={handleLogin}>
                <input 
                  type="email" 
                  placeholder="Enter email (e.g. ihab.awad@lu.ac.ae)" 
                  value={email} 
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
                {error && <p style={{color: 'red', fontSize: '14px', fontWeight: 'bold'}}>{error}</p>}
                <button type="submit" className="btn-primary">Sign In to Portal</button>
              </form>
            </div>
          </div>

          {/* SECTION 2: STATS BAR */}
          <div className="stats-bar">
            <div className="stat-item">
              <div className="stat-icon">🏛️</div>
              <div className="stat-text">
                <h4>Liwa University</h4>
                <p>Licensed by the MOHESR in UAE</p>
              </div>
            </div>
            <div className="stat-item">
              <div className="stat-icon">🎓</div>
              <div className="stat-text">
                <h4>Graduate & Undergraduate</h4>
                <p>35 Programs Available</p>
              </div>
            </div>
            <div className="stat-item">
              <div className="stat-icon">📍</div>
              <div className="stat-text">
                <h4>Campuses in</h4>
                <p>Abu Dhabi and Al Ain</p>
              </div>
            </div>
          </div>

          {/* SECTION 3: ABOUT (Fixed to match original site) */}
          <div className="about-section">
            <div className="about-logo-placeholder">
              🛡️
            </div>
            <div className="about-content">
              <h2>About Liwa University</h2>
              <p>Since 1993, Liwa University has been a cornerstone of higher education in the Emirate of Abu Dhabi, nurturing potential and supplying the UAE labor market with skilled professionals.</p>
              <p>Today, we are proud to unveil our rebranded institution! As we embark on this exciting journey, our mission remains to serve our local community by providing exceptional education and cultivating the next generation of leaders. Success starts with you!</p>
              <button className="btn-primary" style={{width: '200px', marginTop: '10px'}}>Read More</button>
            </div>
          </div>

          {/* SECTION 4: SERVICES (Fixed with Icons) */}
          <div className="services-section">
            <div className="service-card">
              <span className="service-icon-top">📚</span>
              <h3>Education Services</h3>
              <p>Liwa University is dedicated to providing exceptional Education Services to students, providing comprehensive courses and programs in various disciplines to foster holistic learning.</p>
              <a>Learn More →</a>
            </div>
            <div className="service-card">
              <span className="service-icon-top">🎓</span>
              <h3>Our Programs</h3>
              <p>We offer a range of Graduate & Undergraduate programs designed to provide students with quality education and equip them with the skills and knowledge necessary for success.</p>
              <a>Learn More →</a>
            </div>
            <div className="service-card">
              <span className="service-icon-top">🏛️</span>
              <h3>University Life</h3>
              <p>At Liwa University, students enjoy vibrant and enriching experiences. We provide a supportive and inclusive environment where students can engage in extracurricular activities.</p>
              <a>Learn More →</a>
            </div>
            <div className="service-card">
              <span className="service-icon-top">🌍</span>
              <h3>International Students</h3>
              <p>By fostering an atmosphere of cultural exchange, Liwa University provides a unique opportunity for students to learn from one another’s backgrounds and perspectives.</p>
              <a>Learn More →</a>
            </div>
          </div>

          {/* SECTION 5: PARTNERS (NEW) */}
          <div className="partners-section">
            <div className="partners-grid">
              {/* Using generated placeholder images to look like logos */}
              <div className="partner-logo-box"><img src="https://placehold.co/180x80/ffffff/004d99?text=Kanad+Hospital" alt="Kanad" /></div>
              <div className="partner-logo-box"><img src="https://placehold.co/180x80/ffffff/000066?text=Istanbul+Aydin+Univ" alt="Istanbul Univ" /></div>
              <div className="partner-logo-box"><img src="https://placehold.co/180x80/ffffff/cc0000?text=Abu+Dhabi+University" alt="AD Univ" /></div>
              <div className="partner-logo-box"><img src="https://placehold.co/180x80/ffffff/b30047?text=Burjeel+Hospital" alt="Burjeel" /></div>
              <div className="partner-logo-box"><img src="https://placehold.co/180x80/ffffff/660066?text=Al+Ain+Club" alt="Al Ain Club" /></div>
            </div>
            <div className="carousel-dots">
              <div className="dot active"></div>
              <div className="dot"></div>
              <div className="dot"></div>
            </div>
          </div>
        </>
      )}


      {/* ---------------- APP DASHBOARD VIEWS ---------------- */}
      
      {view === 'classes' && (
        <div className="app-container">
          <div className="content-box">
            <h2 className="section-title">Faculty Dashboard</h2>
            <h3 style={{color: '#555', marginTop: 0}}>Welcome, {facultyName}</h3>
            <p>Select a class below to manage student attendance.</p>
            <div style={{overflowX: 'auto'}}>
              <table>
                <thead>
                  <tr>
                    <th>Class Nbr</th><th>Class ID</th><th>Semester</th><th>Course Code</th>
                    <th>Course Name</th><th>Start Time</th><th>End Time</th>
                    <th>Campus Name</th><th>Room ID</th>
                  </tr>
                </thead>
                <tbody>
                  {classes.map((cls, idx) => (
                    <tr key={idx} className="clickable-row" onClick={() => fetchStudents(cls['Class Nbr'])}>
                      <td>{cls['Class Nbr']}</td>
                      <td>{cls['Cass ID']}</td>
                      <td>{cls['Semester']}</td>
                      <td>{cls['Course Code']}</td>
                      <td>{cls['Course Name']}</td>
                      <td>{cls['Start Time']}</td>
                      <td>{cls['End Time']}</td>
                      <td>{cls['Campus Name']}</td>
                      <td>{cls['Room ID']}</td>
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
            <h2 className="section-title">Class Roster: {selectedClass}</h2>
            <p>List of students currently enrolled in this course.</p>
            <div style={{maxWidth: '800px'}}>
              <table>
                <thead>
                  <tr>
                    <th>Student ID</th>
                    <th>Student Name</th>
                    <th>Action (AI Pending)</th>
                  </tr>
                </thead>
                <tbody>
                  {students.map((student, idx) => (
                    <tr key={idx}>
                      <td>{student['Student ID']}</td>
                      <td>{student['Student Name']}</td>
                      <td>
                        <button style={{padding: '6px 12px', backgroundColor: '#e9ecef', border: '1px solid #ccc', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold', color: '#333'}}>
                          Verify Attendance
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ---------------- FOOTER ---------------- */}
      <footer className="site-footer">
        <div className="footer-grid">
          <div>
            <h3 style={{borderBottom: '2px solid #ffcb05', display: 'inline-block', paddingBottom: '5px'}}>🛡️ Liwa University</h3>
            <p style={{color: '#ccc', lineHeight: '1.6'}}>
              Abu Dhabi Campus<br/>
              Saeed Bin Ahmed Al Otaiba Street<br/>
              PO Box 41009, Abu Dhabi, UAE
            </p>
          </div>
          <div>
            <h3>Contact us</h3>
            <ul style={{color: '#ccc'}}>
              <li>Call Center: 600 500606</li>
              <li>E-mail: info@lu.ac.ae</li>
            </ul>
          </div>
          <div>
            <h3>Quick links</h3>
            <ul>
              <li><a>→ Academic Calendar</a></li>
              <li><a>→ Scholarships & Financial Aid</a></li>
              <li><a>→ Bachelor's Programs</a></li>
              <li><a>→ Careers</a></li>
            </ul>
          </div>
          <div>
            <h3>Follow us</h3>
            <div className="social-icons">
              <span>in</span><span>▶</span><span>f</span><span>𝕏</span>
            </div>
          </div>
        </div>
        <div style={{textAlign: 'center', color: '#888', paddingTop: '10px'}}>
          © 2026 Liwa University AI Attendance System. All rights reserved.
        </div>
      </footer>

    </div>
  );
}

export default App;