import urllib.request
import os
import ssl

# Disable SSL verification if corporate proxy blocks it (optional, safe for this)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Primary: OpenCV Zoo direct CDN link (more reliable than raw GitHub)
urls = [
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "https://github.com/opencv/opencv_zoo/releases/download/0.1/face_detection_yunet_2023mar.onnx"
]

filename = "face_detection_yunet_2023mar.onnx"

# Remove broken file if it exists
if os.path.exists(filename):
    os.remove(filename)
    print("Removed old/corrupted file")

for i, url in enumerate(urls):
    try:
        print(f"\nTrying source {i+1}...")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
            data = response.read()
            if len(data) < 100000:  # Model should be ~350KB
                print(f"  Got only {len(data)} bytes — skipping")
                continue
            
            with open(filename, 'wb') as f:
                f.write(data)
            print(f"✅ SUCCESS: Downloaded {len(data)} bytes")
            print(f"Saved to: {os.path.abspath(filename)}")
            break
    except Exception as e:
        print(f"  Failed: {e}")
else:
    print("\n❌ All automatic downloads failed.")
    print("Please download manually from:")
    print("https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet")
    print("Click the ONNX file → 'Download' or 'View raw'")