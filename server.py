import mss
import cv2
import numpy as np
import eventlet
import pyautogui  # Critical fix: added this
from flask import Flask
from flask_socketio import SocketIO

# Mandatory for eventlet background tasks
eventlet.monkey_patch()

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Variable to track orientation
rotation_state = "landscape"

@socketio.on('rotate_display')
def handle_rotation(data):
    global rotation_state
    rotation_state = data['orientation']
    print(f"🔄 Switching to {rotation_state}")

def capture_and_stream():
    global rotation_state
    with mss.mss() as sct:
        # monitor[2] is your extended screen
        monitor = sct.monitors[2]
        
        while True:
            # 1. Capture Screen
            sct_img = sct.grab(monitor)
            frame = np.array(sct_img)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            
            # 2. Draw the Mouse (Must happen BEFORE compression)
            mx, my = pyautogui.position()
            rx, ry = mx - monitor["left"], my - monitor["top"]

            if 0 <= rx < monitor["width"] and 0 <= ry < monitor["height"]:
                # Drawing a cursor that's visible on any background
                cv2.circle(frame, (rx, ry), 8, (255, 255, 255), -1) # White core
                cv2.circle(frame, (rx, ry), 9, (0, 0, 0), 2)        # Black outline

            # 3. Handle Rotation
            if rotation_state == "portrait":
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)

            # 4. Fast JPEG Compression
            _, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
            
            # 5. Emit Binary Data
            socketio.emit('screen_frame', buffer.tobytes())
            socketio.sleep(0.01) 

@app.route('/')
def index():
    return """
    <html>
        <body style="margin:0; background:black; overflow:hidden;">
            <canvas id="screenCanvas" style="width:100vw; height:100vh; object-fit:contain;"></canvas>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
            <script>
                const socket = io();
                const canvas = document.getElementById('screenCanvas');
                const ctx = canvas.getContext('2d');

                // Handle Physical Rotation
                window.screen.orientation.addEventListener('change', function() {
                    let mode = window.screen.orientation.type.includes('portrait') ? 'portrait' : 'landscape';
                    socket.emit('rotate_display', { orientation: mode });
                });

                socket.on('screen_frame', (data) => {
                    const blob = new Blob([data], { type: 'image/jpeg' });
                    const url = URL.createObjectURL(blob);
                    const img = new Image();
                    img.onload = () => {
                        canvas.width = img.width;
                        canvas.height = img.height;
                        ctx.drawImage(img, 0, 0);
                        URL.revokeObjectURL(url);
                    };
                    img.src = url;
                });
            </script>
        </body>
    </html>
    """

if __name__ == '__main__':
    socketio.start_background_task(capture_and_stream)
    socketio.run(app, host='0.0.0.0', port=5001)