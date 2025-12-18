import mss
import base64
from flask import Flask, render_template
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

def capture_and_stream():
    with mss.mss() as sct:
        # Define the monitor to capture (usually 1)
        monitor = sct.monitors[2]
        
        while True:
            # 1. Capture Screen
            img = sct.grab(monitor)
            
            # 2. Convert to JPEG (Ultra-fast compression)
            img_bytes = mss.tools.to_png(img.rgb, img.size) # Or use OpenCV for faster JPG
            
            # 3. Encode to Base64
            encoded = base64.b64encode(img_bytes).decode('utf-8')
            
            # 4. Push to all connected devices (iPad/Old PC)
            socketio.emit('screen_frame', {'image': encoded})
            socketio.sleep(0.01) # Aiming for ~60-90 FPS

@app.route('/')
def index():
    return """
    <html>
        <body style="margin:0; background:black;">
            <canvas id="screenCanvas" style="width:100vw; height:100vh; object-fit:contain;"></canvas>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
            <script>
                const canvas = document.getElementById('screenCanvas');
                const ctx = canvas.getContext('2d');
                const socket = io();
                socket.on('screen_frame', (data) => {
                    const img = new Image();
                    img.onload = () => {
                        canvas.width = img.width;
                        canvas.height = img.height;
                        ctx.drawImage(img, 0, 0);
                    };
                    img.src = 'data:image/png;base64,' + data.image;
                });
            </script>
        </body>
    </html>
    """

if __name__ == '__main__':
    socketio.start_background_task(capture_and_stream)
    socketio.run(app, host='0.0.0.0', port=5001)
