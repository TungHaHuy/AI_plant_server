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

# --- TÀI KHOẢN LOGIN (NHÉT CỨNG) ---
TB_USERNAME = "tys2k3@gmail.com"
TB_PASSWORD = "Hahoangquan123"
# -----------------------------------

# --- Biến để lưu token tự động ---
g_tb_jwt_token = None
g_tb_login_lock = threading.Lock() # Lock cho việc login

last_pump_state = None
is_manual_mode = False

# ==========================================================
#  RECIPES (Không đổi)
# ==========================================================
PLANT_RECIPES = {
    "Fruit_and_Ripening": { "target_soil": 70, "rgb_color": (255, 0, 32), "brightness": 255, "light_hours": 12, "temp_day": (20, 22), "temp_night": (15, 18), "humi_day": (50, 50), "humi_night": (70, 80) },
    "Flowering": { "target_soil": 70, "rgb_color": (255, 0, 64), "brightness": 255, "light_hours": 12, "temp_day": (20, 24), "temp_night": (16, 18), "humi_day": (45, 55), "humi_night": (60, 70) },
    "Vegetative": { "target_soil": 60, "rgb_color": (255, 0, 64), "brightness": 223, "light_hours": 14, "temp_day": (22, 26), "temp_night": (18, 20), "humi_day": (50, 60), "humi_night": (70, 80) },
    "Seeding": { "target_soil": 60, "rgb_color": (200, 200, 255), "brightness": 159, "light_hours": 14, "temp_day": (25, 26), "temp_night": (18, 20), "humi_day": (45, 55), "humi_night": (80, 80) },
    "Idle_Empty": { "target_soil": 0, "rgb_color": (0, 0, 0), "brightness": 0, "light_hours": 0, "temp_day": (0, 100), "temp_night": (0, 100), "humi_day": (0, 100), "humi_night": (0, 100) }
}

current_stage = "Idle_Empty"
current_recipe = PLANT_RECIPES[current_stage]
current_day_state = "IDLE"

lock = threading.RLock() # Dùng RLock
scheduler = BackgroundScheduler(daemon=True)
app = Flask(__name__)

try:
    scheduler.start()
    print("Scheduler started.")
    atexit.register(lambda: scheduler.shutdown())
except Exception as e:
    print("Scheduler error:", e)

# ==========================================================
#  HÀM LOGIN TỰ ĐỘNG
# ==========================================================
def auto_login_and_get_jwt():
    """
    Tự động login vào ThingsBoard để lấy JWT.
    """
    global g_tb_jwt_token, g_tb_login_lock

    with g_tb_login_lock:
        if g_tb_jwt_token:
            return g_tb_jwt_token

        print("\n[AUTH] Đang login để lấy JWT token mới...")
        url = f"{TB_API}/api/auth/login"
        payload = {"username": TB_USERNAME, "password": TB_PASSWORD}
        
        try:
            r = requests.post(url, json=payload, timeout=5)
            
            if r.status_code == 200:
                data = r.json()
                g_tb_jwt_token = data.get("token")
                print("[AUTH] Login thành công, đã có token mới.")
                return g_tb_jwt_token
            else:
                print(f"[AUTH] LỖI LOGIN: {r.status_code} {r.text}")
                return None
        except Exception as e:
            print(f"[AUTH] LỖI NGOẠI LỆ KHI LOGIN: {e}")
            return None

def get_jwt():
    """Hàm helper để lấy token, nếu chưa có sẽ tự động login."""
    if g_tb_jwt_token:
        return g_tb_jwt_token
    return auto_login_and_get_jwt()

def invalidate_jwt():
    """Hàm này được gọi khi gặp lỗi 401, buộc lần gọi sau phải login lại."""
    global g_tb_jwt_token
    print("[AUTH] Token đã hết hạn (401). Xóa token cũ.")
    g_tb_jwt_token = None

# ==========================================================
#  API: GET SERVER ATTRIBUTE 'mode' (Đã sửa)
# ==========================================================
def get_mode_from_server():
    url = f"{TB_API}/api/plugins/telemetry/DEVICE/{DEVICE_ID}/values/attributes/SERVER_SCOPE?keys=mode"
    
    token = get_jwt()
    if not token:
        return None 

    headers = {"X-Authorization": f"Bearer {token}"}

    try:
        r = requests.get(url, headers=headers, timeout=3)
        
        if r.status_code == 401:
            invalidate_jwt() # Token hết hạn, xóa nó đi
            return None

        if r.status_code != 200:
            print(f"[MODE API] ERROR {r.status_code}: {r.text}")
            return None

        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return bool(data[0].get("value"))
        if isinstance(data, dict) and "mode" in data:
            arr = data.get("mode")
            if isinstance(arr, list) and len(arr) > 0:
                return bool(arr[0].get("value"))
        return None
    except Exception as e:
        print(f"[MODE API] EXCEPTION: {e}")
        return None

# ==========================================================
#  BACKGROUND CHECK (Không đổi)
# ==========================================================
def background_manual_sync():
    global is_manual_mode
    mode_from_server = get_mode_from_server()
    if mode_from_server is None:
        return 
    current_python_mode = is_manual_mode
    if mode_from_server != current_python_mode:
        print(f"\n--- 🔄 CHẾ ĐỘ THAY ĐỔI: {mode_from_server} ---")
        is_manual_mode = mode_from_server 
        if current_python_mode == True and mode_from_server == False:
            print("[SYNC] Đã tắt chế độ thủ công. Đang khôi phục đồng hồ...")
            with lock:
                current_hour = datetime.now().hour
                recipe = current_recipe
                if current_stage == "Idle_Empty":
                    return
                light_hours = recipe.get("light_hours", 12)
                clear_all_jobs() 
                if 0 <= current_hour < light_hours:
                    print(f"[SYNC] Giờ {current_hour} -> BAN NGÀY. Gọi go_to_day().")
                    go_to_day(start_hour=current_hour) 
                else:
                    print(f"[SYNC] Giờ {current_hour} -> BAN ĐÊM. Gọi go_to_night().")
                    go_to_night(is_idle=False, start_hour=current_hour)

scheduler.add_job(background_manual_sync, "interval", seconds=3, id="manual_sync", replace_existing=True)

# ==========================================================
#  RPC (Đã sửa)
# ==========================================================
def send_rpc(method, params):
    global is_manual_mode
    if is_manual_mode:
        print(f"[MANUAL] Block RPC {method} {params}")
        return

    token = get_jwt()
    if not token:
        return 

    url = f"{TB_API}/api/plugins/rpc/oneway/{DEVICE_ID}"
    headers = {"X-Authorization": f"Bearer {token}"}
    payload = {"method": method, "params": params}

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=3)
        print(f"[RPC] {method} -> {r.status_code}")
        if r.status_code == 401:
            invalidate_jwt() # Token hết hạn
    except Exception as e:
        print(f"[RPC ERROR] {e}")

# ==========================================================
#  SEND ATTRIBUTES (Không đổi, dùng DEVICE_TOKEN)
# ==========================================================
def send_attributes(payload):
    url = f"{TB_API}/api/v1/{DEVICE_TOKEN}/attributes"
    try:
        r = requests.post(url, json=payload, timeout=3)
        print(f"[ATTR] {payload} -> {r.status_code}")
    except Exception as e:
        print(f"[ATTR ERROR] {e}")

# ==========================================================
#  DAY/NIGHT (Không đổi)
# ==========================================================
def go_to_day(start_hour=0):
    global current_day_state
    with lock:
        if current_stage == "Idle_Empty":
            return
        print(f"\n--- ☀️ PLANT DAYTIME (Start Hour: {start_hour}) ---")
        current_day_state = "DAY"
        recipe = current_recipe
        print("[CLOCK] Đảm bảo nguồn LED Bật")
        send_rpc("setLedPower", {"state": True}) 
        r, g, b = recipe["rgb_color"]
        send_rpc("setLedColor", {"ledR": r, "ledG": g, "ledB": b})
        send_rpc("setBrightness", {"brightness": recipe["brightness"]})
        min_t, max_t = recipe["temp_day"]
        min_h, max_h = recipe["humi_day"]
        send_attributes({ "min_temp": min_t, "max_temp": max_t, "min_humi": min_h, "max_humi": max_h, "day_cycle": "DAY" })
        light_hours = recipe.get("light_hours", 12)
        remaining = light_hours - start_hour
        run_time = datetime.now() + timedelta(hours=max(remaining, 0.01))
        try:
            scheduler.add_job(go_to_night, 'date', run_date=run_time, id='night_job', replace_existing=True)
            print(f"[CLOCK] Lên lịch TẮT ĐÈN sau {remaining:.1f} giờ")
        except Exception as e:
            print(f"[CLOCK ERROR] Lỗi add_job night: {e}")

def go_to_night(is_idle=False, start_hour=None):
    global current_day_state
    with lock:
        recipe = current_recipe
        if is_idle:
            print(f"\n--- 💤 PLANT IDLE ---")
            current_day_state = "IDLE"
        else:
            print(f"\n--- 🌙 PLANT NIGHTTIME (Start Hour: {start_hour}) ---")
            current_day_state = "NIGHT"
        send_rpc("setPump", {"state": False})
        send_rpc("setLedPower", {"state": False})
        min_t, max_t = recipe["temp_night"]
        min_h, max_h = recipe["humi_night"]
        send_attributes({ "min_temp": min_t, "max_temp": max_t, "min_humi": min_h, "max_humi": max_h, "day_cycle": current_day_state })
        if not is_idle:
            light_hours = recipe.get("light_hours", 12)
            remaining = (24 - start_hour) if start_hour is not None else (24 - light_hours)
            run_time = datetime.now() + timedelta(hours=max(remaining, 0.01))
            try:
                scheduler.add_job(go_to_day, 'date', run_date=run_time, id='day_job', replace_existing=True)
                print(f"[CLOCK] Lên lịch BẬT ĐÈN sau {remaining:.1f} giờ")
            except Exception as e:
                print(f"[CLOCK ERROR] Lỗi add_job day: {e}")

def clear_all_jobs():
    print("[CLOCK] Hủy tất cả lịch trình (day_job/night_job)...")
    try:
        if scheduler.get_job("day_job"): scheduler.remove_job("day_job")
        if scheduler.get_job("night_job"): scheduler.remove_job("night_job")
    except Exception as e:
        print(f"[CLOCK ERROR] Lỗi khi xóa job: {e}")

# ==========================================================
#  UPDATE STAGE (Không đổi)
# ==========================================================
def update_stage_internal(new_stage):
    global current_stage, current_recipe, last_pump_state
    with lock:
        if new_stage not in PLANT_RECIPES: return
        if new_stage == current_stage: return
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
#  HOME (Không đổi)
# ==========================================================
@app.route("/")
def home():
    return f"AI Plant Server running — Stage {current_stage} ({current_day_state}), manual={is_manual_mode}"

# ==========================================================
#  WEBHOOK (Không đổi)
# ==========================================================
@app.route("/roboflow_webhook", methods=["POST"])
def roboflow_webhook():
    data = request.json
    print("\n--- Webhook received ---")
    preds = data.get("predictions", [])
    if isinstance(preds, dict): preds = preds.get("predictions", [])
    if not preds: new_stage = "Idle_Empty"
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
#  SENSOR DATA (Không đổi)
# ==========================================================
@app.route("/process_data", methods=["POST"])
def process_data():
    global last_pump_state
    data = request.json
    try:
        soil = float(data.get("soil"))
        temp = float(data.get("temperature"))
        humi = float(data.get("humidity"))
    except Exception as e:
        return jsonify({"error": "Invalid data"}), 400
    with lock:
        recipe = current_recipe
        day_state = current_day_state
    print(f"\n--- Soil Check --- soil={soil}, temp={temp}, humi={humi}")
    if day_state == "DAY":
        min_h, max_h = recipe["humi_day"]
        min_t, max_t = recipe["temp_day"]
    else:
        min_h, max_h = recipe["humi_night"]
        min_t, max_t = recipe["temp_night"]
    target = recipe["target_soil"]
    soil_state = -1 if soil < target else (1 if soil > target else 0)
    humi_state = -1 if humi < min_h else (1 if humi > max_h else 0)
    temp_state = -1 if temp < min_t else (1 if temp > max_t else 0)
    send_attributes({ "soil_state": soil_state, "humi_state": humi_state, "temp_state": temp_state })
    if is_manual_mode:
        print("[PUMP] Manual mode -> skip pump")
        return jsonify({"status": "manual"}), 200
    if target == 0: desired = False
    else: desired = (soil_state == -1)
    if last_pump_state != desired:
        print(f"[PUMP] State changed -> sending RPC: {desired}")
        send_rpc("setPump", {"state": desired})
        last_pump_state = desired
    else:
        print(f"[PUMP] State unchanged ({desired}) -> no RPC sent")
    return jsonify({"status": "pump on" if desired else "pump off"}), 200

# ==========================================================
#  API SET GIỜ (Không đổi)
# ==========================================================
@app.route("/set_manual_time", methods=["POST"])
def set_manual_time():
    data = request.json
    hour = data.get("hour")
    if hour is None: return jsonify({"error": "Missing 'hour'"}), 400
    try:
        hour = int(hour)
        if not (0 <= hour <= 23): raise ValueError("Giờ phải từ 0-23")
    except Exception as e: return jsonify({"error": str(e)}), 400
    print(f"\n--- ⚙️ SET GIỜ THỦ CÔNG: {hour}:00 ---")
    with lock:
        if current_stage == "Idle_Empty":
            go_to_night(is_idle=True)
            return jsonify({"status": "idle, setting night"}), 200
        if is_manual_mode:
             print(f"[MANUAL TIME] Đang ở chế độ thủ công. RPC sẽ bị chặn.")
        clear_all_jobs()
        recipe = current_recipe
        light_hours = recipe.get("light_hours", 12)
        if 0 <= hour < light_hours:
            go_to_day(start_hour=hour)
        else:
            go_to_night(is_idle=False, start_hour=hour)
    return jsonify({"status": "ok", "set_hour": hour, "stage": current_stage}), 200

# ==========================================================
#  RUN
# ==========================================================
if __name__ == "__main__":
    # Tự động login 1 lần ngay khi khởi động
    if TB_USERNAME and TB_PASSWORD:
        print("Đang thực hiện login lần đầu tiên khi khởi động...")
        auto_login_and_get_jwt()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
