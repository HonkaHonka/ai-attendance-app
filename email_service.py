# email_service.py — Gmail App Password (SMTP) version
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Your Gmail credentials
GMAIL_USER = "aminopmarzouk@gmail.com"      # ← Change this
GMAIL_APP_PASSWORD = "ikyo tmaw kwqd xiij"  # ← Paste your 16-char App Password (remove spaces)

def send_verification_email(to_email: str, teacher_name: str, verify_url: str):
    """Send verification email using Gmail SMTP + App Password."""
    try:
        subject = " Liwa University - Verify Your Login"
        
        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #2f3254; padding: 20px; text-align: center;">
                <h1 style="color: #ffcb05; margin: 0;">🛡️ Liwa University</h1>
            </div>
            <div style="padding: 30px; background: #f9f9f9;">
                <h2 style="color: #2f3254;">Hello {teacher_name},</h2>
                <p>We received a login request for the AI Attendance System.</p>
                <p><strong>Click the button below to verify and access your courses:</strong></p>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{verify_url}" 
                       style="background: #ffcb05; color: #2f3254; padding: 15px 30px; 
                              text-decoration: none; border-radius: 8px; font-weight: bold;
                              display: inline-block; font-size: 16px;">
                        ✅ Verify & Login
                    </a>
                </div>
                <p style="color: #888; font-size: 13px;">
                    This link expires in 10 minutes. If you didn't request this, ignore this email.
                </p>
                <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                <p style="color: #aaa; font-size: 12px; text-align: center;">
                    Liwa University Faculty Portal<br>
                    📧 info@lu.ac.ae | 📞 600 500606
                </p>
            </div>
        </body>
        </html>
        """
        
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = GMAIL_USER
        msg['To'] = to_email
        
        msg.attach(MIMEText(body, 'html'))
        
        # Send via Gmail SMTP
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())
        
        print(f"📧 Verification email sent to {to_email}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        return False