import urllib.request
import os

# A list of mirror servers hosting the open-source YOLOv8-Face model
mirrors =[
    "https://huggingface.co/junjiang/GestureFace/resolve/main/yolov8n-face.pt",
    "https://huggingface.co/SynthAIzer/kaggle/resolve/main/yolov8n-face.pt",
    "https://github.com/akanametov/yolo-face/releases/download/v1.0.0/yolov8n-face.pt"
]

print("Downloading YOLOv8-Face model...")

success = False
for url in mirrors:
    try:
        print(f"Trying mirror: {url}")
        # Add a fake User-Agent so servers don't block the Python script
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with urllib.request.urlopen(req) as response, open("yolov8n-face.pt", 'wb') as out_file:
            out_file.write(response.read())
            
        print("✅ Download complete! Saved as yolov8n-face.pt")
        success = True
        break # Stop trying if it succeeds!
        
    except Exception as e:
        print(f"❌ Mirror failed ({e}). Trying next...")

if not success:
    print("All mirrors failed. Let me know and I will provide a direct Google Drive link!")