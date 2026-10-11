
from flask import Flask, request, jsonify
import serial
import serial.tools.list_ports
import threading
import time
import webbrowser
import socket

app = Flask(__name__)

BAUD_RATE = 115200
FRAME_SIZE = 128 * 64 // 8  # 1024 bytes

arduino = None
serial_lock = threading.Lock()


def find_arduino():
    """Try to find the Arduino automatically."""
    ports = list(serial.tools.list_ports.comports())

    for port in ports:
        description = (port.description or "").lower()
        manufacturer = (port.manufacturer or "").lower()

        if any(word in description + " " + manufacturer
               for word in ["ch340", "arduino", "usb serial", "usb-serial"]):
            return port.device

    if ports:
        print("\nAvailable serial ports:")
        for port in ports:
            print(f"  {port.device}: {port.description}")
        print("Set SERIAL_PORT manually in the script if needed.")

    return None


# If automatic detection fails, replace None with your port, e.g. "COM3".
SERIAL_PORT = None


def connect_arduino():
    global arduino

    port = SERIAL_PORT or find_arduino()

    if not port:
        print("Arduino port not found. Connect the Nano and restart.")
        return

    try:
        arduino = serial.Serial(port, BAUD_RATE, timeout=1,
                                write_timeout=2)
        time.sleep(2)  # Nano may reset when serial opens
        arduino.reset_input_buffer()
        print(f"Connected to Arduino on {port}")
    except serial.SerialException as exc:
        print(f"Could not open {port}: {exc}")
        print("Close Arduino Serial Monitor and restart this script.")


def send_serial(packet):
    with serial_lock:
        if arduino is None or not arduino.is_open:
            return False

        try:
            arduino.write(packet)
            arduino.flush()
            return True
        except (serial.SerialException, serial.SerialTimeoutException):
            return False


@app.route("/")
def home():
    return PAGE


@app.route("/frame", methods=["POST"])
def receive_frame():
    data = request.get_data()

    if len(data) != FRAME_SIZE:
        return jsonify(error="Expected exactly 1024 bytes"), 400

    # Full-frame protocol: F followed by 1024 packed OLED bytes.
    ok = send_serial(b"F" + data)
    return jsonify(ok=ok), (200 if ok else 503)


@app.route("/clear", methods=["POST"])
def clear_screen():
    ok = send_serial(b"C")
    return jsonify(ok=ok), (200 if ok else 503)


@app.route("/invert", methods=["POST"])
def invert_screen():
    value = request.get_json(silent=True) or {}
    enabled = bool(value.get("enabled", False))

    # The Arduino's invert command is followed by ASCII 0 or 1.
    ok = send_serial(b"I" + (b"1" if enabled else b"0"))
    return jsonify(ok=ok), (200 if ok else 503)


PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1, maximum-scale=1,
               user-scalable=no">
<title>OLED Drawing Pad</title>
<style>
* { box-sizing: border-box; }
body {
  margin: 0; padding: 18px;
  background: #111; color: #f5f5f5;
  font-family: system-ui, sans-serif;
}
h2 { margin: 0 0 6px; }
p { color: #aaa; font-size: 14px; }
.panel { max-width: 600px; margin: auto; }
canvas {
  width: 100%; height: auto; aspect-ratio: 2 / 1;
  display: block; background: black;
  border: 2px solid #555; border-radius: 10px;
  touch-action: none; image-rendering: pixelated;
  margin: 16px 0;
}
canvas.inverted { filter: invert(1); }
.tools { display: flex; flex-wrap: wrap; gap: 9px; }
button {
  border: 1px solid #555; border-radius: 10px;
  padding: 11px 15px; color: white;
  background: #292929; font-size: 15px;
}
button.active { background: #276749; border-color: #4ade80; }
label {
  display: block; margin: 15px 0 6px;
  color: #ddd; font-size: 14px;
}
input[type=range] { width: 100%; }
.status { min-height: 22px; font-size: 13px; color: #9ae6b4; }
</style>
</head>
<body>
<div class="panel">
  <h2>OLED Drawing Pad</h2>
  <p>Draw here and your Arduino Nano OLED will mirror it.</p>

  <canvas id="pad" width="128" height="64"></canvas>

  <div class="tools">
    <button id="pen" class="active">✏️ Pen</button>
    <button id="eraser">🧽 Eraser</button>
    <button id="invert">Invert</button>
    <button id="clear">Clear All</button>
  </div>

  <label>Pen width: <span id="penVal">2</span> px</label>
  <input id="penWidth" type="range" min="1" max="12" value="2">

  <label>Eraser width: <span id="eraseVal">6</span> px</label>
  <input id="eraseWidth" type="range" min="1" max="20" value="6">

  <div class="status" id="status">Ready</div>
</div>

<script>
const canvas = document.getElementById('pad');
const ctx = canvas.getContext('2d', {willReadFrequently: true});
const statusEl = document.getElementById('status');

let tool = 'pen';
let inverted = false;
let drawing = false;
let last = null;
let sendTimer = null;
let sending = false;
let dirty = false;

let penWidth = 2;
let eraseWidth = 6;

ctx.fillStyle = 'black';
ctx.fillRect(0, 0, 128, 64);
ctx.lineCap = 'round';
ctx.lineJoin = 'round';

function setStatus(text) {
  statusEl.textContent = text;
}

function chooseTool(which) {
  tool = which;
  document.getElementById('pen').classList.toggle('active', which === 'pen');
  document.getElementById('eraser').classList.toggle('active', which === 'eraser');
}

document.getElementById('pen').onclick = () => chooseTool('pen');
document.getElementById('eraser').onclick = () => chooseTool('eraser');

document.getElementById('penWidth').oninput = event => {
  penWidth = Number(event.target.value);
  document.getElementById('penVal').textContent = penWidth;
};

document.getElementById('eraseWidth').oninput = event => {
  eraseWidth = Number(event.target.value);
  document.getElementById('eraseVal').textContent = eraseWidth;
};

function position(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(127,
      (event.clientX - rect.left) * 128 / rect.width)),
    y: Math.max(0, Math.min(63,
      (event.clientY - rect.top) * 64 / rect.height))
  };
}

function markDirty() {
  dirty = true;
  if (!sendTimer) {
    sendTimer = setTimeout(() => {
      sendTimer = null;
      sendFrame();
    }, 120);
  }
}

function draw(from, to) {
  ctx.strokeStyle = tool === 'pen' ? 'white' : 'black';
  ctx.fillStyle = ctx.strokeStyle;
  ctx.lineWidth = tool === 'pen' ? penWidth : eraseWidth;

  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.lineTo(to.x, to.y);
  ctx.stroke();

  // Ensure a tap without movement creates a dot.
  ctx.beginPath();
  ctx.arc(to.x, to.y, ctx.lineWidth / 2, 0, Math.PI * 2);
  ctx.fill();

  markDirty();
}

canvas.addEventListener('pointerdown', event => {
  event.preventDefault();
  drawing = true;
  last = position(event);
  canvas.setPointerCapture(event.pointerId);
  draw(last, last);
});

canvas.addEventListener('pointermove', event => {
  if (!drawing) return;
  event.preventDefault();
  const next = position(event);
  draw(last, next);
  last = next;
});

function stopDrawing() {
  drawing = false;
  last = null;
  if (dirty && !sendTimer && !sending) sendFrame();
}
canvas.addEventListener('pointerup', stopDrawing);
canvas.addEventListener('pointercancel', stopDrawing);
canvas.addEventListener('lostpointercapture', stopDrawing);

function packFrame() {
  const pixels = ctx.getImageData(0, 0, 128, 64).data;
  const frame = new Uint8Array(1024);

  for (let y = 0; y < 64; y++) {
    for (let x = 0; x < 128; x++) {
      const i = (y * 128 + x) * 4;
      const isWhite = pixels[i] > 127;
      const pixelOn = inverted ? !isWhite : isWhite;

      if (pixelOn) {
        const byteIndex = x + Math.floor(y / 8) * 128;
        frame[byteIndex] |= 1 << (y % 8);
      }
    }
  }
  return frame;
}

async function sendFrame() {
  if (sending || !dirty) return;
  sending = true;
  dirty = false;

  try {
    const frame = packFrame();
    const response = await fetch('/frame', {
      method: 'POST',
      headers: {'Content-Type': 'application/octet-stream'},
      body: frame
    });

    if (!response.ok) throw new Error('Arduino not connected');
    setStatus('Connected · drawing synced');
  } catch (error) {
    dirty = true;
    setStatus('Connection error — check Arduino and Python terminal');
  } finally {
    sending = false;
    if (dirty && !sendTimer) {
      sendTimer = setTimeout(() => {
        sendTimer = null;
        sendFrame();
      }, 120);
    }
  }
}

document.getElementById('invert').onclick = async () => {
  inverted = !inverted;
  canvas.classList.toggle('inverted', inverted);
  markDirty();
};

document.getElementById('clear').onclick = async () => {
  if (sendTimer) {
    clearTimeout(sendTimer);
    sendTimer = null;
  }

  ctx.fillStyle = 'black';
  ctx.fillRect(0, 0, 128, 64);
  dirty = false;

  try {
    const response = await fetch('/clear', {method: 'POST'});
    if (!response.ok) throw new Error();
    setStatus('Canvas and OLED cleared');
  } catch {
    setStatus('Clear failed — check Arduino connection');
  }
};
</script>
</body>
</html>
"""


if __name__ == "__main__":
    connect_arduino()

    # Print a local-network address for your phone.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except OSError:
        local_ip = "YOUR_LAPTOP_IP"

    print("\nOpen this address on your phone:")
    print(f"  http://{local_ip}:5000")
    print("Keep this terminal open. Press Ctrl+C to stop.\n")

    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)
