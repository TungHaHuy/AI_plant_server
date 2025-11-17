from flask import Flask, request, jsonify
import requests
import threading
import atexit
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import os

# ==========================================================
#  CONFIG
# ==========================================================
TB_API = "https://thingsboard.cloud"

DEVICE_ID = "6cc4a260-bbeb-11f0-8f6e-0181075d8a82"
DEVICE_TOKEN = "fNsd0L35ywAKakJ979b2"

# JWT ADMIN TOKEN (FROM DEVTOOLS)
TB_JWT_TOKEN = "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0eXMyazNAZ21haWwuY29tIiwidXNlcklkIjoiYWU2NjQxODAtYmJlNC0xMWYwLTkxYWQtMDljYTUyZDJkZDkxIiwic2NvcGVzIjpbIlRFTkFOVF9BRE1JTiJdLCJzZXNzaW9uSWQiOiI1Zjc0MTAyNC01ZDIzLTRmZTEtYjczMi03NzM5MzhmNzFiOTEiLCJleHAiOjE3NjM0MDE3MjcsImlzcyI6InRoaW5nc2JvYXJkLmNsb3VkIiwiaWF0IjoxNzYzMzcyOTI3LCJmaXJzdE5hbWUiOiJUeXMiLCJlbmFibGVkIjp0cnVlLCJpc1B1YmxpYyI6ZmFsc2UsImlzQmlsbGluZ1NlcnZpY2UiOmZhbHNlLCJwcml2YWN5UG9saWN5QWNjZXB0ZWQiOnRydWUsInRlcm1zT2ZVc2VBY2NlcHRlZCI6dHJ1ZSwidGVuYW50SWQiOiJhZTNjZTc5MC1iYmU0LTExZjAtOTFhZC0wOWNhNTJkMmRkOTEiLCJjdXN0b21lcklkIjoiMTM4MTQwMDAtMWRkMi0xMWIyLTgwODAtODA4MDgwODA4MDgwIn0.Zm_9fX4lsKRWSvBVrnT5q2hNUQvx7rkZEvVt7zXUFzNFCeh413wUQbevKd7o3ntT48Z8F1AGkRPPA0knGin1kA"

# Mode cache – giảm số lần gọi API
_cached_mode = None
_last_mode_fetch = 0


# ==========================================================
#  GET MODE ONLY WHEN NEEDED
# ==========================================================
import time

def get_mode():
    global _cached_mode, _last_mode_fetch

    # cache trong 1 giây để tránh spam
    if time.time() - _last_mode_fetch < 1:
        return _cached_mode

    url = f"{TB_API}/api/plugins/telemetry/DEVICE/{DEVICE_ID}/values/attributes/SERVER_SCOPE?keys=mode"
    headers = {"X-Authorization": f"Bearer {TB_JWT_TOKEN}"}

    try:
        r = requests.get(url, headers=headers, timeout=3)
        if r.status_code != 200:
            print("[MODE] ERROR:", r.text)
            return _cached_mode

        data = r.json()

        # TB Cloud trả dạng list
        if isinstance(data, list) and len(data) > 0:
            _cached_mode = bool(data[0].get("value"))

        _last_mode_fetch = time.time()
        print(f"[MODE] = {_cached_mode}")
        return _cached_mode

    except Exception as e:
        print("[MODE] EXCEPTION:", e)
        return _cached_mode


# ==========================================================
#  RECIPES
# ==========================================================
PLANT_RECIPES = {
    "Fruit_and_Ripening": {
        "target_soil": 70, "rgb_color": (255, 0, 32), "brightness": 255, "light_hours": 12,
        "temp_day": (20, 22), "temp_night": (15, 18), "humi_day": (50, 50), "humi_night": (70, 80)
    },
    "Flowering": {
        "target_soil": 70, "rgb_color": (255, 0, 64), "brightness": 255, "light_hours": 12,
        "temp_day": (20, 24), "temp_night": (16, 18), "humi_day": (45, 55), "humi_night": (60, 70)
    },
    "Vegetative": {
        "target_soil": 60, "rgb_color": (255, 0, 64), "brightness": 223, "light_hours": 14,
        "temp_day": (22, 26), "temp_night": (18, 20), "humi_day": (50, 60), "humi_night": (70, 80)
    },
    "Seeding": {
        "target_soil": 60, "rgb_color": (200, 200, 255), "brightness": 159, "light_hours": 14,
        "temp_day": (25, 26), "temp_night": (18, 20), "humi_day": (45, 55), "humi_night": (80, 80)
    },
    "Idle_Empty": {
        "target_soil": 0, "rgb_color": (0, 0, 0), "brightness": 0, "light_hours": 0,
        "temp_day": (0, 100), "temp_night": (0, 100), "humi_day": (0, 100), "humi_night": (0, 100)
    }
}

current_stage = "Idle_Empty"
current_recipe = PLANT_RECIPES[current_stage]
current_day_state = "IDLE"
last_pump_state = None

app = Flask(__name__)



# ==========================================================
#  RPC (mode check HERE)
# ==========================================================
def send_rpc(method, params):

    if get_mode() is True:
        print(f"[MANUAL] SKIP RPC {method}")
        return

    url = f"{TB_API}/api/plugins/rpc/oneway/{DEVICE_ID}"
    headers = {"X-Authorization": f"Bearer {TB_JWT_TOKEN}"}
    payload = {"method": method, "params": params}

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=3)
        print(f"[RPC] {method} -> {r.status_code}")
    except Exception as e:
        print("[RPC ERROR]", e)



# ==========================================================
#  SEND ATTRIBUTES
# ==========================================================
def send_attributes(payload):
    url = f"{TB_API}/api/v1/{DEVICE_TOKEN}/attributes"
    try:
        requests.post(url, json=payload, timeout=3)
    except:
        pass



# ==========================================================
#  DAY / NIGHT
# ==========================================================
def go_to_day():
    global current_day_state

    if current_stage == "Idle_Empty":
        return

    current_day_state = "DAY"
    r, g, b = current_recipe["rgb_color"]

    send_rpc("setLedColor", {"ledR": r, "ledG": g, "ledB": b})
    send_rpc("setBrightness", {"brightness": current_recipe["brightness"]})

    min_t, max_t = current_recipe["temp_day"]
    min_h, max_h = current_recipe["humi_day"]
    send_attributes({
        "min_temp": min_t, "max_temp": max_t,
        "min_humi": min_h, "max_humi": max_h,
        "day_cycle": "DAY"
    })


def go_to_night(is_idle=False):
    global current_day_state

    current_day_state = "IDLE" if is_idle else "NIGHT"

    send_rpc("setPump", {"state": False})
    send_rpc("setLedPower", {"state": False})

    min_t, max_t = current_recipe["temp_night"]
    min_h, max_h = current_recipe["humi_night"]
    send_attributes({
        "min_temp": min_t, "max_temp": max_t,
        "min_humi": min_h, "max_humi": max_h,
        "day_cycle": current_day_state
    })



# ==========================================================
#  UPDATE STAGE
# ==========================================================
def update_stage_internal(new_stage):
    global current_stage, current_recipe, last_pump_state

    current_stage = new_stage
    current_recipe = PLANT_RECIPES[new_stage]
    last_pump_state = None

    if new_stage == "Idle_Empty":
        go_to_night(is_idle=True)
    else:
        go_to_day()



# ==========================================================
#  HOME
# ==========================================================
@app.route("/")
def home():
    return f"Plant Server — Stage {current_stage}, mode={get_mode()}"


# ==========================================================
#  WEBHOOK
# ==========================================================
@app.route("/roboflow_webhook", methods=["POST"])
def webhook():
    data = request.json
    preds = data.get("predictions", [])

    if isinstance(preds, dict):
        preds = preds.get("predictions", [])

    if not preds:
        stage = "Idle_Empty"
    else:
        found = {p["class"] for p in preds if p.get("confidence", 0) > 0.4}
        if "Fruit_and_Ripening" in found or "Fruiting" in found:
            stage = "Fruit_and_Ripening"
        elif "Flowering" in found:
            stage = "Flowering"
        elif "Vegetative" in found:
            stage = "Vegetative"
        elif "Seeding" in found:
            stage = "Seeding"
        else:
            stage = "Idle_Empty"

    print("[WEBHOOK] →", stage)
    threading.Thread(target=update_stage_internal, args=(stage,)).start()

    return jsonify({"stage": stage})


# ==========================================================
#  SENSOR DATA (AUTO PUMP)
# ==========================================================
@app.route("/process_data", methods=["POST"])
def process_data():
    global last_pump_state

    data = request.json
    soil = float(data.get("soil"))
    temp = float(data.get("temperature"))
    humi = float(data.get("humidity"))

    recipe = current_recipe

    # Determine states
    if current_day_state == "DAY":
        min_h, max_h = recipe["humi_day"]
        min_t, max_t = recipe["temp_day"]
    else:
        min_h, max_h = recipe["humi_night"]
        min_t, max_t = recipe["temp_night"]

    soil_state = -1 if soil < recipe["target_soil"] else (1 if soil > recipe["target_soil"] else 0)

    # Pump rule
    if recipe["target_soil"] == 0:
        desired = False
    else:
        desired = (soil_state == -1)

    if desired != last_pump_state:
        send_rpc("setPump", {"state": desired})
        last_pump_state = desired

    return jsonify({"pump": desired})


# ==========================================================
#  RUN
# ==========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
