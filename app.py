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
# BẠN SẼ CẦN CẬP NHẬT CÁI NÀY KHI NÓ HẾT HẠN
TB_JWT_TOKEN = "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0eXMyazNAZ21haWwuY29tIiwidXNlcklkIjoiYWU2NjQxODAtYmJlNC0xMWYwLTkxYWQtMDljYTUyZDJkZDkxIiwic2NvcGVzIjpbIlRFTkFOVF9BRE1JTiJdLCJzZXNzaW9uSWQiOiJkMzFhMjg1OS0xMTUzLTRiZDQtODI0Yy04YmE2MDAyNjI0MjciLCJleHAiOjE3NjM0MzE5NTEsImlzcyI6InRoaW5nc2JvYXJkLmNsb3VkIiwiaWF0IjoxNzYzNDAzMTUxLCJmaXJzdE5hbWUiOiJUeXMiLCJlbmFibGVkIjp0cnVlLCJpc1B1YmxpYyI6ZmFsc2UsImlzQmlsbGluZ1NlcnZpY2UiOmZhbHNlLCJwcml2YWN5UG9saWN5QWNjZXB0ZWQiOnRydWUsInRlcm1zT2ZVc2VBY2NlcHRlZCI6dHJ1ZSwidGVuYW50SWQiOiJhZTNjZTc5MC1iYmU0LTExZjAtOTFhZC0wOWNhNTJkMmRkOTEiLCJjdXN0b21lcklkIjoiMTM4MTQwMDAtMWRkMi0xMWIyLTgwODAtODA4MDgwODA4MDgwIn0.HJQWoWFRzu1Rc4ZGJMF_F3VKGY3b_bZS6CW_IuHNumE34xJ8cvxMCQmEDFBcW_oR4oOoKtKZY8dh-tate2G9FQ"
last_pump_state = None
is_manual_mode = False  # <<<<<<<<<< MANUAL MODE FLAG

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

lock = threading.RLock() # <<< DÙNG RLock
scheduler = BackgroundScheduler(daemon=True)
app = Flask(__name__)

try:
    scheduler.start()
    print("Scheduler started.")
    atexit.register(lambda: scheduler.shutdown())
except Exception as e:
    print("Scheduler error:", e)


# ==========================================================
#  API: GET MODE (*** ĐÃ BỊ XÓA ***)
# ==========================================================
# def get_mode_from_server(): ... (ĐÃ XÓA)


# ==========================================================
#  BACKGROUND CHECK (*** ĐÃ BỊ XÓA ***)
# ==========================================================
# def background_manual_sync(): ... (ĐÃ XÓA)

# scheduler.add_job(background_manual_sync, ...) (ĐÃ XÓA)


# ==========================================================
#  HELPER: SYNC ĐỒNG HỒ KHI TẮT MANUAL (*** HÀM MỚI ***)
# ==========================================================
def sync_clock_state():
    """Hàm này khôi phục lại trạng thái Day/Night sau khi tắt manual"""
    print("[SYNC] Đã tắt chế độ thủ công. Đang khôi phục đồng hồ...")
    # Dùng lock để đảm bảo an toàn luồng
    with lock: 
        current_hour = datetime.now().hour
        recipe = current_recipe
        if current_stage == "Idle_Empty":
            print("[SYNC] Đang Idle, không cần khôi phục.")
            return

        light_hours = recipe.get("light_hours", 12)
        
        clear_all_jobs() # Xóa job cũ đi

        if 0 <= current_hour < light_hours:
            print(f"[SYNC] Giờ {current_hour} là BAN NGÀY. Gọi go_to_day().")
            go_to_day(start_hour=current_hour) 
        else:
            print(f"[SYNC] Giờ {current_hour} là BAN ĐÊM. Gọi go_to_night().")
            go_to_night(is_idle=False, start_hour=current_hour)

# ==========================================================
#  API: SET MANUAL MODE (*** HÀM ĐÃ SỬA LỖI BOOL("false") ***)
# ==========================================================
@app.route("/set_manual_mode", methods=["POST"])
def set_manual_mode_api():
    global is_manual_mode
    data = request.json
    
    # Dữ liệu 100% là string "true" hoặc string "false"
    # (Dựa trên log của bạn, không phải giao diện)
    
    # Lấy param, đổi nó sang string, và viết thường
    new_mode_str = str(data.get("params")).lower() # Chuyển "False" -> "false"

    if new_mode_str not in ["true", "false"]:
        print(f"[MODE API] Lỗi: 'params' không phải 'true'/'false', mà là: {new_mode_str}")
        return jsonify({"error": "Invalid params"}), 400

    # *** DÒNG SỬA LỖI LÀ ĐÂY ***
    # So sánh string "true" thay vì ép kiểu bool()
    new_mode_bool = (new_mode_str == "true") 
    
    # Chỉ xử lý nếu có thay đổi
    if new_mode_bool != is_manual_mode:
        print(f"\n--- ⚙️ MODE SET VIA API: {new_mode_bool} ---")
        is_manual_mode = new_mode_bool
        
        # Nếu vừa TẮT manual (chuyển sang False)
        if is_manual_mode == False:
            threading.Thread(target=sync_clock_state, daemon=True).start()

    return jsonify({"status": "ok", "manual_mode": is_manual_mode}), 200


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
        if r.status_code == 401:
             print("\n\n*** LỖI: TOKEN ĐÃ HẾT HẠN. HÃY DÁN TOKEN MỚI VÀO CODE. ***\n\n")
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
    
    # Lấy lock để đảm bảo không bị xung đột
    with lock:
        if current_stage == "Idle_Empty":
            print("[CLOCK] Bỏ qua go_to_day() vì đang Idle.")
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
        send_attributes({
            "min_temp": min_t, "max_temp": max_t,
            "min_humi": min_h, "max_humi": max_h,
            "day_cycle": "DAY"
        })

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
        send_rpc("setLedPower", {"state": False}) # Tắt đèn

        min_t, max_t = recipe["temp_night"]
        min_h, max_h = recipe["humi_night"]
        send_attributes({
            "min_temp": min_t, "max_temp": max_t,
            "min_humi": min_h, "max_humi": max_h,
            "day_cycle": current_day_state
        })

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
        if scheduler.get_job("day_job"):
            scheduler.remove_job("day_job")
        if scheduler.get_job("night_job"):
            scheduler.remove_job("night_job")
    except Exception as e:
        print(f"[CLOCK ERROR] Lỗi khi xóa job: {e}")


# ==========================================================
#  UPDATE STAGE
# ==========================================================
def update_stage_internal(new_stage):
    global current_stage, current_recipe, last_pump_state

    with lock:
        if new_stage not in PLANT_RECIPES:
            print(f"Lỗi: Không tìm thấy stage '{new_stage}'")
            return
        
        if new_stage == current_stage:
            print(f"[STAGE] Vẫn là {new_stage}, không thay đổi.")
            return

        print(f"[STAGE] {current_stage} -> {new_stage}")
        current_stage = new_stage
        current_recipe = PLANT_RECIPES[current_stage]
        last_pump_state = None

        clear_all_jobs()

        if new_stage == "Idle_Empty":
            go_to_night(is_idle=True)
        else:
            go_to_day(start_hour=0) # Bắt đầu ngày mới từ giờ 0


# ==========================================================
#  HOME
# ==========================================================
@app.route("/")
def home():
    return f"AI Plant Server running — Stage {current_stage} ({current_day_state}), manual={is_manual_mode}"


# ==========================================================
#  WEBHOOK (bất đồng bộ)
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

    # Chạy trong luồng riêng để trả về 200 OK ngay lập tức
    threading.Thread(target=update_stage_internal, args=(new_stage,)).start()

    return jsonify({"status":"queued","stage":new_stage}), 200


# ==========================================================
#  SENSOR DATA (auto bơm)
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
        print(f"[PROCESS DATA] Lỗi parse JSON: {e}")
        return jsonify({"error": "Invalid data"}), 400

    with lock:
        recipe = current_recipe
        day_state = current_day_state

    print(f"\n--- Soil Check --- soil={soil}, temp={temp}, humi={humi}")

    if day_state == "DAY":
        min_h, max_h = recipe["humi_day"]
        min_t, max_t = recipe["temp_day"]
    else: # NIGHT hoặc IDLE
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
        print(f"[PUMP] State changed -> sending RPC: {desired}")
        send_rpc("setPump", {"state": desired})
        last_pump_state = desired
    else:
        print(f"[PUMP] State unchanged ({desired}) -> no RPC sent")

    return jsonify({"status": "pump on" if desired else "pump off"}), 200


# ==========================================================
#  API SET GIỜ
# ==========================================================
@app.route("/set_manual_time", methods=["POST"])
def set_manual_time():
    data = request.json
    hour = data.get("hour") # Lấy giờ (0-23)
    
    if hour is None:
        return jsonify({"error": "Missing 'hour'"}), 400
    
    try:
        hour = int(hour)
        if not (0 <= hour <= 23):
                raise ValueError("Giờ phải từ 0-23")
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    
    print(f"\n--- ⚙️ SET GIỜ THỦ CÔNG: {hour}:00 ---")

    with lock:
        if current_stage == "Idle_Empty":
            print(f"[MANUAL TIME] Bỏ qua, đang Idle.")
            go_to_night(is_idle=True) 
            return jsonify({"status": "idle, setting night"}), 200
        
        if is_manual_mode:
             print(f"[MANUAL TIME] Đang ở chế độ thủ công. Sẽ chạy, nhưng RPC (đèn) sẽ bị chặn.")

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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
