from flask import Flask, jsonify, request
import sys
import time

app = Flask(__name__)

# Get port from command line
if len(sys.argv) < 2:
    print("Usage: python app.py <port>")
    sys.exit(1)

PORT = int(sys.argv[1])

SERVER_NAME = f"Backend-{PORT}"

# /slow sleeps for 5 seconds by default; /slow?ms=300 sleeps for 300 ms.
DEFAULT_SLOW_MS = 5000
MAX_SLOW_MS = 30000


@app.route("/")
def home():
    return jsonify({
        "server": SERVER_NAME,
        "port": PORT,
        "message": "Hello from backend server"
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "server": SERVER_NAME,
        "port": PORT
    }), 200


@app.route("/slow")
def slow():
    # Simulate a slow backend. The delay can be changed with ?ms=<number>
    # (0 to 30000); the default is 5000 ms.
    try:
        delay_ms = int(request.args.get("ms", DEFAULT_SLOW_MS))
    except ValueError:
        return jsonify({"error": "ms must be a whole number"}), 400

    delay_ms = max(0, min(delay_ms, MAX_SLOW_MS))

    time.sleep(delay_ms / 1000)

    return jsonify({
        "server": SERVER_NAME,
        "port": PORT,
        "delay_ms": delay_ms,
        "message": "Slow response completed"
    })


if __name__ == "__main__":
    print(f"Starting {SERVER_NAME} on port {PORT}")

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )
