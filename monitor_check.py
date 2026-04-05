import time
import zlib
import Quartz
import ctypes
import socket
import json

class DisplayEngine:
    def __init__(self, width=1920, height=1080):
        self.width = width
        self.height = height
        self.is_portrait = False
        self.buffer = None

    def create_framebuffer(self):
        bytes_per_row = self.width * 4
        total_bytes = bytes_per_row * self.height
        self.buffer = ctypes.create_string_buffer(total_bytes)
        
        color_space = Quartz.CGColorSpaceCreateDeviceRGB()
        context = Quartz.CGBitmapContextCreate(
            self.buffer, self.width, self.height, 8,
            bytes_per_row, color_space, Quartz.kCGImageAlphaPremultipliedLast
        )
        
        if context:
            print(f"✅ Virtual Buffer Created: {self.width}x{self.height}")
            return context
        return None

    def handle_rotation_signal(self, angle):
        if angle in [90, 270]:
            if not self.is_portrait:
                self.width, self.height = self.height, self.width
                self.is_portrait = True
        else:
            if self.is_portrait:
                self.width, self.height = self.height, self.width
                self.is_portrait = False
        
        print(f"🔄 Re-aligning Buffer to: {self.width}x{self.height}")
        return self.create_framebuffer()
    
    def capture_screen(self):
        """Captures the main screen and returns it as a CGImage."""
        # Grab the 'Main' display (usually ID 0 or the first in the list)
        main_display = Quartz.CGMainDisplayID()
        
        # Create a screenshot of the display
        image = Quartz.CGDisplayCreateImage(main_display)
        
        if image:
            width = Quartz.CGImageGetWidth(image)
            height = Quartz.CGImageGetHeight(image)
            print(f"📸 Captured Frame: {width}x{height}")
            return image
        return None
    
    def capture_to_buffer(self):
        """Grabs the screen and returns the raw image data."""
        main_display = Quartz.CGMainDisplayID()
        image = Quartz.CGDisplayCreateImage(main_display)
        
        if image:
            # Get raw data provider
            data_provider = Quartz.CGImageGetDataProvider(image)
            raw_data = Quartz.CGDataProviderCopyData(data_provider)
            
            # This is the "Gold": actual pixel bytes
            # In a real app, we'd send these bytes over the network
            buffer_size = Quartz.CFDataGetLength(raw_data)
            print(f"📸 Frame Captured! Size: {buffer_size} bytes")
            
            compressed = zlib.compress(raw_data, level=1) # Level 1 is fastest
            print(f"📉 Compressed Size: {len(compressed)} bytes")
            print(f"⚡ Ratio: {len(raw_data) / len(compressed):.1f}x smaller")
            return raw_data
        return None

def start_listener():
    """Kept outside the class for cleaner execution."""
    engine = DisplayEngine()
    engine.create_framebuffer()

    UDP_IP = "0.0.0.0"
    UDP_PORT = 5005

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))

    print(f"🚀 Listener active on {UDP_IP}:{UDP_PORT}")
    print("Send JSON like: {'angle': 90}")

    try:
        while True:
            data, addr = sock.recvfrom(1024) 
            try:
                message = json.loads(data.decode('utf-8'))
                if "angle" in message:
                    angle = int(message["angle"])
                    engine.handle_rotation_signal(angle)
            except Exception as e:
                print(f"Ignoring bad packet: {e}")
                
    except KeyboardInterrupt:
        print("\nStopping Server...")
    finally:
        sock.close()


def start_streaming(target_ip="127.0.0.1"):
    engine = DisplayEngine()
    engine.create_framebuffer()
    
    UDP_PORT = 5006
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    print(f"📡 Starting Stream to {target_ip}:{UDP_PORT}...")
    
    try:
        while True:
            start_time = time.time()
            
            # 1. Capture & Compress
            raw_data = engine.capture_to_buffer()
            compressed = zlib.compress(raw_data, level=1)
            
            # 2. Send (Note: UDP has a limit, we might need to chunk this later)
            # For now, let's just see how fast the loop runs locally
            # sock.sendto(compressed, (target_ip, UDP_PORT))
            
            end_time = time.time()
            fps = 1 / (end_time - start_time)
            print(f"🚀 FPS: {fps:.1f} | Size: {len(compressed)/1024:.0f} KB")
            
    except KeyboardInterrupt:
        print("\nStream Stopped.")

if __name__ == "__main__":
    start_streaming()
