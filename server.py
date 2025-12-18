import mss
import cv2
import numpy as np
import base64
from flask import Flask
from flask_socketio import SocketIO
import eventlet

app = Flask(__name__)
# Using binary=True for maximum speed
socketio = SocketIO(app, cors_allowed_origins="*")

def capture_and_stream():
    with mss.mss() as sct:
        # Change to 1 for Main Screen, 2 for Virtual Screen
        monitor = sct.monitors[2]
        
        while True:
            # 1. Capture
            sct_img = sct.grab(monitor)
            frame = np.array(sct_img)
            
            # 2. Convert Color (MSS is BGRA, we need BGR)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            
            # 3. PORTRAIT ROTATION (Uncomment the line below to rotate 90 degrees)
            # frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)

            # 4. Ultra-Fast JPEG Compression
            # Quality 60 is the sweet spot for 75Hz vibes
            _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
            
            # 5. EMIT AS RAW BYTES
            socketio.emit('screen_frame', buffer.tobytes())
            socketio.sleep(0.01) # Low sleep = High FPS

@app.route('/')
def index():
    return """
    <html>
        <body style="margin:0; background:black; overflow:hidden;">
            <canvas id="screenCanvas" style="width:100vw; height:100vh; object-fit:contain;"></canvas>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
            <script>
                const canvas = document.getElementById('screenCanvas');
                const ctx = canvas.getContext('2d');
                const socket = io();

                socket.on('screen_frame', (data) => {
                    // Receive raw binary data as a Blob
                    const blob = new Blob([data], { type: 'image/jpeg' });
                    const url = URL.createObjectURL(blob);
                    
                    const img = new Image();
                    img.onload = () => {
                        canvas.width = img.width;
                        canvas.height = img.height;
                        ctx.drawImage(img, 0, 0);
                        URL.revokeObjectURL(url); // Clean up memory
                    };
                    img.src = url;
                });
            </script>
        </body>
    </html>
    """

if __name__ == '__main__':
    eventlet.spawn(capture_and_stream)
    socketio.run(app, host='0.0.0.0', port=5001)    