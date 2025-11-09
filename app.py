from flask import Flask, request, jsonify
import requests
import threading

# ======================
# CONFIG
# ======================
TB_HOST = "https://thingsboard.cloud"
DEVICE_TOKEN = "fNsd0L35ywAKakJ979b2"  # THAY TOKEN KHÁC THÌ ĐỔI DÒNG NÀY

PLANT_RECIPES = {
    "Fruit_and_Ripening": { "target_soil": 50, "light_r": 255, "light_g": 50,  "light_b": 0 },
    "Flowering":          { "target_soil": 55, "light_r": 255, "light_g": 0,   "light_b": 150 },
    "Vegetative":         { "target_soil": 60, "light_r": 200, "light_g": 50,  "light_b": 200 },
    "Seedling":           { "target_soil": 70, "light_r": 150, "light_g": 150, "light_b": 200 },
    "Idle_Empty":         { "target_soil": 0,  "light_r": 0,   "light_g": 0,   "light_b": 0 }
}

current_stage = "Idle_Empty"
current_recipe = PLANT_RECIPES[current_stage]
session = requests.Session()
lock = threading.Lock()

app = Flask(__name__)


# ======================
# RPC CALLER
# ======================
def send_rpc(method, params):
    url = f"{TB_HOST}/api/v1/{DEVICE_TOKEN}/rpc"
    payload = {"method": method, "params": params}

    # Retry 3 lần tránh Render lag/timeout burst
    for attempt in range(3):
        try:
            r = session.post(url, json=payload, timeout=15)
            print(f"[RPC] {method} -> {r.status_code}")
            return
        except Exception as e:
            print(f"[RPC] FAIL {attempt+1}: {e}")


# ======================
# STAGE CHANGE
# ======================
def update_stage(new_stage):
    global current_stage, current_recipe

    if new_stage not in PLANT_RECIPES:
        return {"error": "invalid stage"}

    if new_stage == current_stage:
        return {"status": "no change"}

    with lock:
        current_stage = new_stage
        current_recipe = PLANT_RECIPES[new_stage]

    print(f"\n=== STAGE → {new_stage} ===")

    light = {
        "r": current_recipe["light_r"],
        "g": current_recipe["light_g"],
        "b": current_recipe["light_b"]
    }

    if light["r"] == 0 and light["g"] == 0 and light["b"] == 0:
        threading.Thread(target=send_rpc, args=("setLedPower", {"state": False}), daemon=True).start()
    else:
        threading.Thread(target=send_rpc, args=("setGrowLight", light), daemon=True).start()

    return {"status": "success", "stage": new_stage}


# ======================
# ROBOTFLOW WEBHOOK
# ======================
def extract_detections(data):
    p = data.get("predictions")
    if isinstance(p, list):
        return p
    if isinstance(p, dict):
        return p.get("predictions", [])
    return []


@app.route("/roboflow_webhook", methods=["POST"])
def roboflow_webhook():
    data = request.json or {}
    detections = extract_detections(data)

    detected = {d.get("class") for d in detections if (d.get("confidence") or 0) > 0.4}

    stage = "Idle_Empty"
    if "Seedling" in detected: stage = "Seedling"
    if "Vegetative" in detected: stage = "Vegetative"
    if "Flowering" in detected: stage = "Flowering"
    if "Fruit_and_Ripening" in detected: stage = "Fruit_and_Ripening"

    return jsonify(update_stage(stage)), 200


# ======================
# ESP32 TELEMETRY LOGIC → SERVER RPC CONTROL
# ======================
@app.route("/process_data", methods=["POST"])
def process_data():
    data = request.json or {}
    soil = data.get("soil")

    if soil is None:
        return jsonify({"error": "missing soil"}), 400

    recipe = current_recipe
    target = recipe["target_soil"]

    if target == 0:
        threading.Thread(target=send_rpc, args=("setPump", {"state": False}), daemon=True).start()
    elif soil < target:
        threading.Thread(target=send_rpc, args=("setPump", {"state": True}), daemon=True).start()
    else:
        threading.Thread(target=send_rpc, args=("setPump", {"state": False}), daemon=True).start()

    return jsonify({"status": "pump rpc ok"}), 200


@app.route("/")
def home():
    return f"AI Plant Server Running (RPC Mode). Stage = {current_stage}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860)
