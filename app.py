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
CAMERA_DEVICE_ID = "2f3ed0d0-c3b8-11f0-a4c6-e5fe644790a2"


# BẠN VẪN CẦN DÁN TOKEN MỚI VÀO ĐÂY KHI NÓ HẾT HẠN
TB_JWT_TOKEN = "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0eXMyazNAZ21haWwuY29tIiwidXNlcklkIjoiYWU2NjQxODAtYmJlNC0xMWYwLTkxYWQtMDljYTUyZDJkZDkxIiwic2NvcGVzIjpbIlRFTkFOVF9BRE1JTiJdLCJzZXNzaW9uSWQiOiI0ZTllNDdjNC03NWYzLTRkYmQtYWY1Zi0yMjJiOGJjYzQxNGQiLCJleHAiOjE3NjM1NTM5NTIsImlzcyI6InRoaW5nc2JvYXJkLmNsb3VkIiwiaWF0IjoxNzYzNTI1MTUyLCJmaXJzdE5hbWUiOiJUeXMiLCJlbmFibGVkIjp0cnVlLCJpc1B1YmxpYyI6ZmFsc2UsImlzQmlsbGluZ1NlcnZpY2UiOmZhbHNlLCJwcml2YWN5UG9saWN5QWNjZXB0ZWQiOnRydWUsInRlcm1zT2ZVc2VBY2NlcHRlZCI6dHJ1ZSwidGVuYW50SWQiOiJhZTNjZTc5MC1iYmU0LTExZjAtOTFhZC0wOWNhNTJkMmRkOTEiLCJjdXN0b21lcklkIjoiMTM4MTQwMDAtMWRkMi0xMWIyLTgwODAtODA4MDgwODA4MDgwIn0.ONzVJjJukRJ2xCtRRdLkMm-R_TWZF3WblzVUwQGjbEja4w2PG9mIKQ3okRIR7qROQTsAvno-QJbhqO71RF-gJw"
last_pump_state = None
is_manual_mode = False

# *** THÊM BIẾN MỚI ĐỂ LƯU GIỜ SINH HỌC ***
g_cycle_start_time = None 

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

lock = threading.RLock() 
scheduler = BackgroundScheduler(daemon=True)
app = Flask(__name__)

try:
    scheduler.start()
    print("Scheduler started.")
    atexit.register(lambda: scheduler.shutdown())
except Exception as e:
    print("Scheduler error:", e)

# ==========================================================
# AUTO CAMERA CAPTURE (every 1 hour)
# ==========================================================
def trigger_camera_capture():
    if is_manual_mode:
        print("[AUTO CAM] Manual mode bật → không chụp ảnh.")
        return

    print("\n=== 📸 AUTO CAPTURE TRIGGER ===")
    send_camera_rpc("takePicture", {})

try:
    scheduler.add_job(
        trigger_camera_capture,
        "interval",
        hours=1,
        id="auto_cam_job",
        replace_existing=True
    )
    print("[AUTO CAM] Auto capture enabled (every 1H).")
except Exception as e:
    print("[AUTO CAM ERROR]", e)

# ==========================================================
#  HELPER: SYNC ĐỒNG HỒ (*** ĐÃ SỬA LỖI LOGIC ***)
# ==========================================================
def sync_clock_state():
    """Hàm này khôi phục lại trạng thái Day/Night DỰA TRÊN GIỜ SINH HỌC"""
    global g_cycle_start_time
    print("[SYNC] Đã tắt chế độ thủ công. Đang khôi phục đồng hồ...")
    
    with lock: 
        recipe = current_recipe
        if current_stage == "Idle_Empty":
            print("[SYNC] Đang Idle, không cần khôi phục.")
            return

        if g_cycle_start_time is None:
            # Server vừa khởi động, không biết giờ bắt đầu.
            # Cứ bắt đầu một chu kỳ mới (Hour 0) ngay bây giờ.
            print("[SYNC] Không tìm thấy giờ bắt đầu (g_cycle_start_time is None). Bắt đầu chu kỳ mới (Hour 0).")
            go_to_day(start_hour=0) # Sẽ tự động set g_cycle_start_time
            return

        # --- LOGIC TƯƠNG ĐỐI (ĐÃ SỬA) ---
        # Tính số giờ đã trôi qua KỂ TỪ LÚC BẮT ĐẦU CHU KỲ (Hour 0)
        elapsed_seconds = (datetime.now() - g_cycle_start_time).total_seconds()
        elapsed_hours = elapsed_seconds / 3600
        
        # Tính giờ "sinh học" hiện tại trong ngày (modulo 24)
        current_bio_hour = elapsed_hours % 24
        
        light_hours = recipe.get("light_hours", 12)
        
        clear_all_jobs() # Xóa job cũ đi

        if 0 <= current_bio_hour < light_hours:
            # Vẫn đang trong giờ ban ngày
            print(f"[SYNC] Giờ sinh học {current_bio_hour:.1f} (trong {light_hours}h) là BAN NGÀY. Gọi go_to_day().")
            go_to_day(start_hour=current_bio_hour) 
        else:
            # Đã qua giờ ban ngày
            print(f"[SYNC] Giờ sinh học {current_bio_hour:.1f} (trong {light_hours}h) là BAN ĐÊM. Gọi go_to_night().")
            go_to_night(is_idle=False, start_hour=current_bio_hour)

# ==========================================================
#  API: SET MANUAL MODE (*** ĐÃ SỬA LỖI BOOL("false") ***)
# ==========================================================
@app.route("/set_manual_mode", methods=["POST"])
def set_manual_mode_api():
    global is_manual_mode
    data = request.json
    
    # Lấy param, đổi nó sang string, và viết thường
    new_mode_str = str(data.get("params")).lower()

    if new_mode_str not in ["true", "false"]:
        print(f"[MODE API] Lỗi: 'params' không phải 'true'/'false', mà là: {new_mode_str}")
        return jsonify({"error": "Invalid params"}), 400

    # So sánh string "true" thay vì ép kiểu bool()
    new_mode_bool = (new_mode_str == "true") 
    
    # Chỉ xử lý nếu có thay đổi
    if new_mode_bool != is_manual_mode:
        print(f"\n--- ⚙️ MODE SET VIA API: {new_mode_bool} ---")
        is_manual_mode = new_mode_bool
        
        # Nếu vừa TẮT manual (chuyển sang False), ta cần khôi phục đồng hồ
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

def send_camera_rpc(method, params):
    global is_manual_mode
    if is_manual_mode:
        print(f"[CAMERA] Manual mode → Block RPC {method}")
        return

    url = f"{TB_API}/api/plugins/rpc/oneway/{CAMERA_DEVICE_ID}"
    headers = {"X-Authorization": f"Bearer {TB_JWT_TOKEN}"}
    payload = {"method": method, "params": params}

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=5)
        print(f"[CAMERA RPC] {method} -> {r.status_code}")
    except Exception as e:
        print(f"[CAMERA RPC ERROR] {e}")

def send_attributes(payload):
    url = f"{TB_API}/api/v1/{DEVICE_TOKEN}/attributes"
    try:
        r = requests.post(url, json=payload, timeout=3)
        print(f"[ATTR] {payload} -> {r.status_code}")
    except:
        pass


# ==========================================================
#  DAY/NIGHT (*** ĐÃ SỬA ĐỂ LƯU GIỜ ***)
# ==========================================================
def go_to_day(start_hour=0):
    global current_day_state, g_cycle_start_time
    
    with lock:
        if current_stage == "Idle_Empty":
            print("[CLOCK] Bỏ qua go_to_day() vì đang Idle.")
            return

        # *** ĐÂY LÀ HÀM LƯU GIỜ ***
        # Nếu đây là một chu kỳ MỚI (start_hour=0)
        # HOẶC chúng ta chưa bao giờ lưu giờ (lần chạy đầu)
        if start_hour == 0 or g_cycle_start_time is None:
            # Trừ đi start_hour (nếu có) để tìm "Hour 0"
            g_cycle_start_time = datetime.now() - timedelta(hours=start_hour)
            print(f"[CLOCK] ĐÃ LƯU GIỜ BẮT ĐẦU CHU KỲ (Hour 0) = {g_cycle_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        # -------------------------

        print(f"\n--- ☀️ PLANT DAYTIME (Start Hour: {start_hour:.1f}) ---")
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
        remaining = light_hours - start_hour # Giờ còn lại của ban ngày
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
            print(f"\n--- 🌙 PLANT NIGHTTIME (Start Hour: {start_hour:.1f}) ---")
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
            
            # start_hour bây giờ là "giờ sinh học" (ví dụ: 19.0)
            # Tổng giờ là 24.
            # Giờ còn lại của ban đêm là 24.0 - 19.0 = 5.0 giờ
            remaining = 24.0 - start_hour
            
            run_time = datetime.now() + timedelta(hours=max(remaining, 0.01))
            try:
                # Lần tới sẽ gọi go_to_day(start_hour=0)
                # Nhưng chúng ta phải dùng lambda để nó không bị gọi ngay
                scheduler.add_job(lambda: go_to_day(start_hour=0), 'date', run_date=run_time, id='day_job', replace_existing=True)
                print(f"[CLOCK] Lên lịch BẬT ĐÈN (Chu kỳ mới) sau {remaining:.1f} giờ")
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
            # Đây là một GIAI ĐOẠN MỚI
            # Bắt đầu "0 giờ sinh học" MỚI ngay bây giờ
            go_to_day(start_hour=0) 


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

@app.route("/upload_photo", methods=["POST"])
def upload_photo():
    data = request.json
    b64 = data.get("photo")

    if not b64:
        return jsonify({"error": "Missing photo"}), 400

    print("[PHOTO] Received photo from ESP32-CAM")

    # Gửi lên Roboflow workflow
    roboflow_url = "https://serverless.roboflow.com/tunghahuy/workflows/custom-workflow"

    payload = {
        "api_key": "YY5sAfysi1GpnWgkVPfF",
        "inputs": {
            "image": {
                "type": "base64",
                "value": b64
            }
        }
    }

    try:
        r = requests.post(roboflow_url, json=payload)
        print("[ROBOFLOW]", r.status_code, r.text)
    except Exception as e:
        print("[ROBOFLOW ERROR]", e)

    return jsonify({"status": "ok"}), 200


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
    
    print(f"\n--- ⚙️ SET GIỜ THỦ CÔNG (TUA ĐỒNG HỒ): {hour}:00 ---")

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
        
        # API này là "tua" đồng hồ sinh học
        # Nó sẽ set 'g_cycle_start_time' về 'hour' tiếng trước
        
        if 0 <= hour < light_hours:
            # Ví dụ: Tua đến giờ 5 (ban ngày)
            # Giờ bắt đầu (Hour 0) là 5 tiếng trước
            go_to_day(start_hour=hour) 
        else:
            # Ví dụ: Tua đến giờ 19 (ban đêm)
            # Giờ bắt đầu (Hour 0) là 19 tiếng trước
            go_to_night(is_idle=False, start_hour=hour)

    return jsonify({"status": "ok", "set_hour": hour, "stage": current_stage}), 200


# ==========================================================
#  RUN
# ==========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
