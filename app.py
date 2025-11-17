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

# JWT ADMIN TOKEN (LẤY TRONG DEVTOOLS)
TB_JWT_TOKEN = "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0eXMyazNAZ21haWwuY29tIiwidXNlcklkIjoiYWU2NjQxODAtYmJlNC0xMWYwLTkxYWQtMDljYTUyZDJkZDkxIiwic2NvcGVzIjpbIlRFTkFOVF9BRE1JTiJdLCJzZXNzaW9uSWQiOiI1Zjc0MTAyNC01ZDIzLTRmZTEtYjczMi03NzM5MzhmNzFiOTEiLCJleHAiOjE3NjM0MDE3MjcsImlzcyI6InRoaW5nc2JvYXJkLmNsb3VkIiwiaWF0IjoxNzYzMzcyOTI3LCJmaXJzdE5hbWUiOiJUeXMiLCJlbmFibGVkIjp0cnVlLCJpc1B1YmxpYyI6ZmFsc2UsImlzQmlsbGluZ1NlcnZpY2UiOmZhbHNlLCJwcml2YWN5UG9saWN5QWNjZXB0ZWQiOnRydWUsInRlcm1zT2ZVc2VBY2NlcHRlZCI6dHJ1ZSwidGVuYW50SWQiOiJhZTNjZTc5MC1iYmU0LTExZjAtOTFhZC0wOWNhNTJkMmRkOTEiLCJjdXN0b21lcklkIjoiMTM4MTQwMDAtMWRkMi0xMWIyLTgwODAtODA4MDgwODA4MDgwIn0.Zm_9fX4lsKRWSvBVrnT5q2hNUQvx7rkZEvVt7zXUFzNFCeh413wUQbevKd7o3ntT48Z8F1AGkRPPA0knGin1kA"
last_pump_state = None
is_manual_mode = False   # <<<<<<<<<< MANUAL MODE FLAG

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

scheduler = BackgroundScheduler(daemon=True)
app = Flask(__name__)

try:
    scheduler.start()
    print("Scheduler started.")
    atexit.register(lambda: scheduler.shutdown())
except Exception as e:
    print("Scheduler error:", e)


# ==========================================================
#  API: GET SERVER ATTRIBUTE 'mode'
# ==========================================================
def get_mode_from_server():
    url = f"{TB_API}/api/plugins/telemetry/DEVICE/{DEVICE_ID}/values/attributes/SERVER_SCOPE?keys=mode"
    headers = {
        "X-Authorization": f"Bearer {TB_JWT_TOKEN}"
    }

    try:
        r = requests.get(url, headers=headers, timeout=3)
        if r.status_code != 200:
            print(f"[MODE API] ERROR {r.status_code}: {r.text}")
            return None

        data = r.json()

        # --- FIX TB CLOUD FORMAT ---#
        if isinstance(data, list) and len(data) > 0:
            return bool(data[0].get("value"))

        print("[MODE API] 'mode' not found in server attributes")
        return None

    except Exception as e:
        print(f"[MODE API] EXCEPTION: {e}")
        return None

# ==========================================================
#  BACKGROUND CHECK: AUTO SYNC MANUAL MODE
# ==========================================================
def background_manual_sync():
    global is_manual_mode

    mode = get_mode_from_server()

    if mode is None:
        return

    if mode != is_manual_mode:
        print("\n------------------------------")
        print(f"🔄 SERVER MODE CHANGED → {mode}")
        print("------------------------------")
        is_manual_mode = mode


scheduler.add_job(background_manual_sync, "interval", seconds=3, id="manual_sync")



# ==========================================================
#  RPC
# ==========================================================
def send_rpc(method, params):
    global is_manual_mode
    if is_manual_mode:
        print(f"[MANUAL] Block RPC {method} {params}")
        return

    url = f"{TB_API}/api/plugins/rpc/oneway/{DEVICE_ID}"
    headers = {"X-Authorization": f"Bearer {TB_JWT_TOKEN}"}
    payload = {"method": method, "params": params}

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=3)
        print(f"[RPC] {method} -> {r.status_code}")
    except Exception as e:
        print(f"[RPC ERROR] {e}")



def send_attributes(payload):
    url = f"{TB_API}/api/v1/{DEVICE_TOKEN}/attributes"
    try:
        r = requests.post(url, json=payload, timeout=3)
        print(f"[ATTR] {payload} -> {r.status_code}")
    except:
        pass


# ==========================================================
#  DAY/NIGHT
# ==========================================================
def go_to_day(start_hour=0):
    global current_day_state
    if current_stage == "Idle_Empty":
        return

    current_day_state = "DAY"
    recipe = current_recipe

    r, g, b = recipe["rgb_color"]
    send_rpc("setLedColor", {"ledR": r, "ledG": g, "ledB": b})
    send_rpc("setBrightness", {"brightness": recipe["brightness"]})

    min_t, max_t = recipe["temp_day"]
    min_h, max_h = recipe["humi_day"]
    send_attributes({
        "min_temp": min_t, "max_temp": max_t,
        "min_humi": min_h, "max_humi": max_h,
        "day_cycle": "DAY"
    })

    light_hours = recipe["light_hours"]
    remaining = light_hours - start_hour
    run_time = datetime.now() + timedelta(hours=max(remaining, 0.01))
    scheduler.add_job(go_to_night, 'date', run_date=run_time, id='night_job')


def go_to_night(is_idle=False, start_hour=None):
    global current_day_state
    recipe = current_recipe

    current_day_state = "IDLE" if is_idle else "NIGHT"

    send_rpc("setPump", {"state": False})
    send_rpc("setLedPower", {"state": False})

    min_t, max_t = recipe["temp_night"]
    min_h, max_h = recipe["humi_night"]
    send_attributes({
        "min_temp": min_t, "max_temp": max_t,
        "min_humi": min_h, "max_humi": max_h,
        "day_cycle": current_day_state
    })

    if not is_idle:
        light_hours = recipe["light_hours"]
        remaining = (24 - start_hour) if start_hour else (24 - light_hours)
        run_time = datetime.now() + timedelta(hours=max(remaining, 0.01))
        scheduler.add_job(go_to_day, 'date', run_date=run_time, id='day_job')


def clear_all_jobs():
    try:
        if scheduler.get_job("day_job"): scheduler.remove_job("day_job")
        if scheduler.get_job("night_job"): scheduler.remove_job("night_job")
    except:
        pass



# ==========================================================
#  UPDATE STAGE
# ==========================================================
def update_stage_internal(new_stage):
    global current_stage, current_recipe, last_pump_state

    if new_stage not in PLANT_RECIPES:
        return

    print(f"[STAGE] {current_stage} -> {new_stage}")
    current_stage = new_stage
    current_recipe = PLANT_RECIPES[current_stage]
    last_pump_state = None

    clear_all_jobs()

    if new_stage == "Idle_Empty":
        go_to_night(is_idle=True)
    else:
        go_to_day(start_hour=0)



# ==========================================================
#  HOME
# ==========================================================
@app.route("/")
def home():
    return f"AI Plant Server running — Stage {current_stage} ({current_day_state}), manual={is_manual_mode}"



# ==========================================================
#  WEBHOOK (đồng bộ, nhẹ, không block)
# ==========================================================
@app.route("/roboflow_webhook", methods=["POST"])
def roboflow_webhook():
    data = request.json
    print("\n--- Webhook received ---")

    preds = data.get("predictions", [])
    if isinstance(preds, dict):
        preds = preds.get("predictions", [])

    if not preds:
        new_stage = "Idle_Empty"
    else:
        found = {p["class"] for p in preds if p.get("confidence",0) > 0.4}
        if "Fruit_and_Ripening" in found or "Fruiting" in found: new_stage = "Fruit_and_Ripening"
        elif "Flowering" in found: new_stage = "Flowering"
        elif "Vegetative" in found: new_stage = "Vegetative"
        elif "Seeding" in found: new_stage = "Seeding"
        else: new_stage = "Idle_Empty"

    print("[WEBHOOK] Stage:", new_stage)

    threading.Thread(target=update_stage_internal, args=(new_stage,)).start()

    return jsonify({"status":"queued","stage":new_stage}), 200



# ==========================================================
#  SENSOR DATA (auto bơm)
# ==========================================================
@app.route("/process_data", methods=["POST"])
def process_data():
    global last_pump_state

    data = request.json
    soil = float(data.get("soil"))
    temp = float(data.get("temperature"))
    humi = float(data.get("humidity"))

    recipe = current_recipe

    print(f"\n--- Soil Check --- soil={soil}, temp={temp}, humi={humi}")

    if current_day_state == "DAY":
        min_h, max_h = recipe["humi_day"]
        min_t, max_t = recipe["temp_day"]
    else:
        min_h, max_h = recipe["humi_night"]
        min_t, max_t = recipe["temp_night"]

    target = recipe["target_soil"]

    soil_state = -1 if soil < target else (1 if soil > target else 0)
    humi_state = -1 if humi < min_h else (1 if humi > max_h else 0)
    temp_state = -1 if temp < min_t else (1 if temp > max_t else 0)

    send_attributes({
        "soil_state": soil_state,
        "humi_state": humi_state,
        "temp_state": temp_state
    })

    if is_manual_mode:
        print("[PUMP] Manual mode -> skip pump")
        return jsonify({"status": "manual"}), 200

    if target == 0:
        desired = False
    else:
        desired = (soil_state == -1)

    if last_pump_state != desired:
        send_rpc("setPump", {"state": desired})
        last_pump_state = desired

    return jsonify({"status": "pump on" if desired else "pump off"}), 200



# ==========================================================
#  RUN
# ==========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
