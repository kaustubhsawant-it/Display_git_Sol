# High-Performance Binary-Stream Display Solution 🚀

A DIY alternative to Apple Sidecar or Spacedesk, built to extend a macOS display to any device with a web browser (optimized for Windows/PC).

## 💡 The Problem
Extending a Mac display to an older Windows laptop often involves laggy third-party software or expensive hardware capture cards. This project explores the limit of **WebSocket-based video streaming** to provide a low-latency, high-refresh-rate second monitor experience.

## 🛠️ Technical Evolution (The "Vibe" Journey)
This project wasn't just built; it was optimized through three distinct phases:

1. **Base64 JSON Overload:** Initial versions used Base64 string encoding. This added ~33% data overhead, causing significant Wi-Fi lag.
2. **The Binary Pivot:** Migrated to a raw **Binary/Blob stream**. By sending raw JPEG bytes, I bypassed the string encoding bottleneck, reducing latency by nearly 40%.
3. **Hardware Bridge Attempt:** Explored a USB-C to USB-C Thunderbolt bridge to create a 10Gbps local network. (Documented hardware handshake limitations with specific laptop chipsets).

## ✨ Key Features
* **Binary WebSocket Streaming:** Ultra-fast frame delivery using `Flask-SocketIO` and `eventlet`.
* **Adaptive Orientation:** JavaScript listeners detect if the receiving device is rotated (Portrait/Landscape) and trigger a `cv2` rotation server-side.
* **Virtual Cursor Rendering:** Since macOS captures often hide the hardware cursor, I implemented a `pyautogui` coordinate mapper to draw a custom cursor onto the frame buffer.
* **Low-Impact Compression:** Uses OpenCV JPEG encoding (Quality 60-70) to balance visual clarity with 60FPS targets.

## 🚀 How to Run
1. Install requirements: `pip install mss opencv-python flask-socketio eventlet pyautogui`
2. Run `python server.py`
3. Open `http://[YOUR_MAC_IP]:5001` on your secondary device.

## 📈 Future Lessons
While this project proved that software optimization can bridge the gap for older hardware, it also highlighted the efficiency of native display protocols. For users seeking a "no-setup" experience, a hardware HDMI dummy plug combined with this software provides the most stable result.

🛠️ What each "Gear" does (For your knowledge)
flask & flask-socketio: The engine that hosts the webpage and handles the "live" connection.
eventlet: This is the "Turbocharger." It allows Python to handle the massive flow of binary image data without crashing the web server.
mss: The "High-Speed Camera." It’s much faster than standard screenshot tools for capturing macOS frames.
opencv-python: The "Processor." This handles the JPEG compression, the 9:16 rotation, and drawing the virtual mouse cursor.
numpy: The "Math Core." OpenCV and MSS use this to handle the image data as a grid of numbers.
pyautogui: The "Radar." This tracks where your mouse is on the Mac so we can draw it on the Windows screen.