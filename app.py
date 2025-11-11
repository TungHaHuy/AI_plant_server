from flask import Flask, request, jsonify
import requests
import threading
import atexit
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import os # <-- Đã có cho Render

# ==========================================================
#  CONFIG (SỬA Ở ĐÂY)
# ==========================================================
TB_API = "https://thingsboard.cloud"

# ID thiết bị (UUID) lấy trong ThingsBoard → Devices → chọn thiết bị → Details
DEVICE_ID = "6cc4a260-bbeb-11f0-8f6e-0181075d8a82"    # <--- SỬA

# JWT Token dài (bạn đã lấy từ API / DevTools)
TB_JWT_TOKEN = "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0eXMyazNAZ21haWwuY29tIiwidXNlcklkIjoiYWU2NjQxODAtYmJlNC0xMWYwLTkxYWQtMDljYTUyZDJkZDkxIiwic2NvcGVzIjpbIlRFTkFOVF9BRE1JTiJdLCJzZXNzaW9uSWQiOiIxNjg4NTExOC1hMGE3LTRmYzktOTcwNS1mMGJjM2NjMWQ3YmEiLCJleHAiOjE3NjI4NTQyODYsImlzcyI6InRoaW5nc2JvYXJkLmNsb3VkIiwiaWF0IjoxNzYyODI1NDg2LCJmaXJzdE5hbWUiOiJUeXMiLCJlbmFibGVkIjp0cnVlLCJpc1B1YmxpYyI6ZmFsc2UsImlzQmlsbGluZ1NlcnZpY2UiOmZhbHNlLCJwcml2YWN5UG9saWN5QWNjZXB0ZWQiOnRydWUsInRlcm1zT2ZVc2VBY2NlcHRlZCI6dHJ1ZSwidGVuYW50SWQiOiJhZTNjZTc5MC1iYmU0LTExZjAtOTFhZC0wOWNhNTJkMmRkOTEiLCJjdXN0bmVySWQiOiIxMzgxQwMDAtMWRkMi0xMWIyLTgwODAtODA4MDgwODA4MDgwIn0.Ahr9rBZdkFQx7O98WS6WFMObMDxIw0NWfLC9cxUdph2eTphHajAe_6m34JjmaLSFoix3eNkDDgG1RViUmRYduw"

# ==========================================================
#  CÁC CÔNG THỨC TRỒNG CÂY
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
    "Seedling": {
        "target_soil": 60, "rgb_color": (200, 200, 255), "brightness": 159, "light_hours": 14,
        "temp_day": (25, 26), "temp_night": (18, 20), "humi_day": (45, 55), "humi_night": (80, 80)
    },
    "Idle_Empty": {
        "target_soil": 0, "rgb_color": (0, 0, 0), "brightness": 0, "light_hours": 0,
        "temp_day": (0, 100), "temp_night": (0, 100), "humi_day": (0, 100), "humi_night": (0, 100)
    }
}

# ==========================================================
# BIẾN TOÀN CỤC VÀ SCHEDULER
# ==========================================================
current_stage = "Idle_Empty"
current_recipe = PLANT_RECIPES[current_stage]
current_day_state = "IDLE" 
lock = threading.Lock()
scheduler = BackgroundScheduler(daemon=True)

app = Flask(__name__)

# ==========================================================
#  KHỞI ĐỘNG SCHEDULER (ĐÃ DI CHUYỂN RA NGOÀI)
# ==========================================================
try:
    scheduler.start()
    print("Scheduler đã khởi động...")
    atexit.register(lambda: scheduler.shutdown())
except Exception as e:
    print(f"Lỗi khởi động Scheduler: {e}")

# ==========================================================
#  HÀM GỬI RPC
# ==========================================================
def send_rpc(method, params):
    url = f"{TB_API}/api/plugins/rpc/oneway/{DEVICE_ID}"
    headers = {"X-Authorization": f"Bearer {TB_JWT_TOKEN}"}
    payload = {"method": method, "params": params}

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=3) 
        print(f"[REST RPC] {method} {params} -> {r.status_code}")
    except Exception as e:
        print(f"[REST RPC ERROR] {method} {params} -> {e}")

# ==========================================================
#  HÀM GỬI ATTRIBUTES (Gửi Ngưỡng)
# ==========================================================
def send_attributes(payload):
    url = f"{TB_API}/api/plugins/telemetry/DEVICE/{DEVICE_ID}/attributes/SHARED_SCOPE"
    headers = {"X-Authorization": f"Bearer {TB_JWT_TOKEN}"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=3)
        print(f"[REST ATTR] {payload} -> {r.status_code}")
    except Exception as e:
        print(f"[REST ATTR ERROR] {payload} -> {e}")

# ==========================================================
#  HÀM TẠO/XÓA ALARM (ĐÃ THÊM)
# ==========================================================
def create_alarm(alarm_type, severity, details):
    """Gửi một Cảnh báo (Alarm) mới lên ThingsBoard."""
    url = f"{TB_API}/api/alarm"
    headers = {"X-Authorization": f"Bearer {TB_JWT_TOKEN}"}
    payload = {
        "name": alarm_type, "severity": severity,
        "originator": {"entityType": "DEVICE", "id": DEVICE_ID},
        "details": details
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=3)
        print(f"[ALARM CREATE] {alarm_type} ({severity}) -> {r.status_code}")
    except Exception as e:
        print(f"[ALARM CREATE ERROR] {e}")

def clear_alarm(alarm_type):
    """Xóa một Cảnh báo đang hoạt động dựa trên loại của nó."""
    url = f"{TB_API}/api/alarm/originator/DEVICE/{DEVICE_ID}/type/{alarm_type}/clear"
    headers = {"X-Authorization": f"Bearer {TB_JWT_TOKEN}"}
    try:
        r = requests.post(url, headers=headers, timeout=3) 
        print(f"[ALARM CLEAR] {alarm_type} -> {r.status_code}")
    except Exception as e:
        print(f"[ALARM CLEAR ERROR] {e}")

# ==========================================================
#  HÀM CHECK ALARM (ĐÃ THÊM)
# ==========================================================
def check_humidity_alarm(current_humi):
    """
    So sánh độ ẩm hiện tại với ngưỡng trong công thức (theo Ngày/Đêm)
    và gửi hoặc xóa alarm.
    """
    global current_recipe, current_day_state
    
    if current_humi is None:
        return 

    try:
        humi_float = float(current_humi)
    except (ValueError, TypeError):
        print(f"[ALARM CHECK] Giá trị độ ẩm không hợp lệ: {current_humi}")
        return

    with lock:
        recipe = current_recipe
        day_state = current_day_state

    if day_state == "IDLE":
        clear_alarm("HUMIDITY_ALARM")
        return

    min_humi, max_humi = (0, 100)
    if day_state == "DAY":
        min_humi, max_humi = recipe["humi_day"]
    else: # (day_state == "NIGHT")
        min_humi, max_humi = recipe["humi_night"]

    alarm_type = "HUMIDITY_ALARM" 
    
    if humi_float < min_humi:
        details = f"Độ ẩm ({humi_float}%) thấp hơn ngưỡng {day_state} ({min_humi}%)"
        print(f"[ALARM CHECK] Gửi cảnh báo: {details}")
        create_alarm(alarm_type, "WARNING", details)
        
    elif humi_float > max_humi:
        details = f"Độ ẩm ({humi_float}%) cao hơn ngưỡng {day_state} ({max_humi}%)"
        print(f"[ALARM CHECK] Gửi cảnh báo: {details}")
        create_alarm(alarm_type, "CRITICAL", details)
        
    else:
        print(f"[ALARM CHECK] Độ ẩm OK ({humi_float}%)")
        clear_alarm(alarm_type)

# ==========================================================
#  LOGIC "ĐỒNG HỒ SINH HỌC"
# ==========================================================
def go_to_day(start_hour=0):
    global current_recipe, current_day_state
    
    if current_stage == "Idle_Empty":
        print("[CLOCK] Bỏ qua go_to_day() vì đang Idle.")
        return

    print(f"\n--- ☀️ PLANT DAYTIME (Start Hour: {start_hour}) ---")
    current_day_state = "DAY"
    recipe = current_recipe 
    
    r, g, b = recipe["rgb_color"]
    brightness = recipe["brightness"]
    send_rpc("setLedColor", {"r": r, "g": g, "b": b})
    send_rpc("setBrightness", {"brightness": brightness})

    min_temp_d, max_temp_d = recipe["temp_day"]
    min_humi_d, max_humi_d = recipe["humi_day"]
    attributes_payload = {
        "min_temp": min_temp_d, "max_temp": max_temp_d,
        "min_humi": min_humi_d, "max_humi": max_humi_d,
        "day_cycle": "DAY"
    }
    send_attributes(attributes_payload)

    light_hours = recipe.get("light_hours", 12)
    remaining_hours = light_hours - start_hour
    if remaining_hours <= 0: remaining_hours = 0.01

    run_time = datetime.now() + timedelta(hours=remaining_hours)
    scheduler.add_job(go_to_night, 'date', run_date=run_time, id='night_job')
    print(f"[CLOCK] Đã lên lịch TẮT ĐÈN sau {remaining_hours:.1f} giờ (lúc {run_time.strftime('%H:%M')})")

def go_to_night(is_idle=False, start_hour=None):
    global current_recipe, current_day_state
    
    recipe = current_recipe 
    
    if is_idle:
        print(f"\n--- 💤 PLANT IDLE ---")
        current_day_state = "IDLE"
    else:
        print(f"\n--- 🌙 PLANT NIGHTTIME (Start Hour: {start_hour}) ---")
        current_day_state = "NIGHT"

    send_rpc("setLedPower", {"state": False}) # Tắt đèn

    min_temp_n, max_temp_n = recipe["temp_night"]
    min_humi_n, max_humi_n = recipe["humi_night"]
    attributes_payload = {
        "min_temp": min_temp_n, "max_temp": max_temp_n,
        "min_humi": min_humi_n, "max_humi": max_humi_n,
        "day_cycle": "NIGHT" if not is_idle else "IDLE"
    }
    send_attributes(attributes_payload)

    if not is_idle:
        light_hours = recipe.get("light_hours", 12)
        
        if start_hour is not None:
            remaining_hours = 24 - start_hour
        else:
            remaining_hours = 24 - light_hours
            
        if remaining_hours <= 0: remaining_hours = 8
        
        run_time = datetime.now() + timedelta(hours=remaining_hours)
        scheduler.add_job(go_to_day, 'date', run_date=run_time, id='day_job')
        print(f"[CLOCK] Đã lên lịch BẬT ĐÈN sau {remaining_hours:.1f} giờ (lúc {run_time.strftime('%H:%M')})")

def clear_all_jobs():
    print("[CLOCK] Hủy tất cả lịch trình (day_job/night_job).")
    try:
        if scheduler.get_job('day_job'):
            scheduler.remove_job('day_job')
        if scheduler.get_job('night_job'):
            scheduler.remove_job('night_job')
    except Exception as e:
        print(f"[CLOCK ERROR] Lỗi khi xóa job: {e}")

# ==========================================================
#  CẬP NHẬT GIAI ĐOẠN PHÁT TRIỂN
# ==========================================================
def update_stage_internal(new_stage):
    global current_stage, current_recipe

    if new_stage not in PLANT_RECIPES:
        print(f"Lỗi: Không tìm thấy stage '{new_stage}' trong PLANT_RECIPES.")
        return {"error": f"Stage '{new_stage}' not found"}

    with lock:
        if current_stage == new_stage:
            return {"status": "no change"}
        
        print(f"\n--- STAGE CHANGED: {current_stage} → {new_stage} ---")
        current_stage = new_stage
        current_recipe = PLANT_RECIPES[current_stage]
        
        clear_all_jobs()

        if new_stage == "Idle_Empty":
            go_to_night(is_idle=True)
        else:
            go_to_day(start_hour=0)

    return {"status": "ok", "stage": current_stage}

# ==========================================================
#  WEB UI CHECK
# ==========================================================
@app.route("/")
def home():
    return f"✅ AI Plant Server is running — Current stage: {current_stage} ({current_day_state})"

# ==========================================================
#  HÀM WORKER CHO WEBHOOK
# ==========================================================
def process_webhook_async(new_stage):
    print(f"[ASYNC WORKER] Bắt đầu xử lý cho stage: {new_stage}")
    update_stage_internal(new_stage)
    print(f"[ASYNC WORKER] Xử lý xong cho stage: {new_stage}")

# ==========================================================
#  WEBHOOK NHẬN KẾT QUẢ TỪ ROBOFLOW (Sửa về đồng bộ)
# ==========================================================
@app.route("/roboflow_webhook", methods=["POST"])
def roboflow_webhook():
    data = request.json
    print("\n--- Received Roboflow Webhook ---")

    predictions_list = []
    if "predictions" in data:
        if isinstance(data["predictions"], list):
            predictions_list = data["predictions"]
        elif isinstance(data["predictions"], dict):
            predictions_list = data["predictions"].get("predictions", [])
    
    if not predictions_list:
        print("No predictions list. Setting to Idle.")
        new_stage = "Idle_Empty"
    else:
        detected_classes = set()
        for p in predictions_list:
            if p.get("confidence", 0) > 0.4:
                detected_classes.add(p.get("class", ""))
        
        print(f"Tất cả class (conf > 0.4): {detected_classes}")

        if not detected_classes:
            print("Tất cả detection đều < 40% confidence. Về Idle.")
            new_stage = "Idle_Empty"
        else:
            new_stage = "Idle_Empty"
            if "Seedling" in detected_classes: new_stage = "Seedling"
            if "Vegetative" in detected_classes: new_stage = "Vegetative"
            if "Flowering" in detected_classes: new_stage = "Flowering"
            if "Fruit_and_Ripening" in detected_classes: new_stage = "Fruit_and_Ripening"
            if "Fruiting" in detected_classes: new_stage = "Fruit_and_Ripening" 

    print(f"[WEBHOOK] Giai đoạn ưu tiên cuối cùng: {new_stage}")
    
    # --- ĐÂY LÀ PHẦN SỬA ---
    # Bỏ scheduler.add_job và process_webhook_async
    # Gọi TRỰC TIẾP (đồng bộ).
    # Chúng ta sẽ "bắt" Roboflow phải chờ
    print("[WEBHOOK] Đang xử lý đồng bộ (chặn Roboflow)...")
    json_response = update_stage_internal(new_stage)
    
    # Trả lời OK sau khi đã xử lý xong
    print("[WEBHOOK] Xử lý đồng bộ XONG. Gửi 200 OK.")
    return jsonify(json_response), 200

# ==========================================================
#  PROCESS SENSOR DATA (ĐÃ CẬP NHẬT)
# ==========================================================
@app.route("/process_data", methods=["POST"])
def process_data():
    data = request.json
    
    soil = data.get("soil")
    temp = data.get("temperature")
    humi = data.get("humidity") 

    if soil is None:
        return jsonify({"error": "Missing 'soil'"}), 400

    try:
        soil_float = float(soil)
    except (ValueError, TypeError):
        return jsonify({"error": f"Invalid 'soil' value: {soil}"}), 400

    # --- PHẦN MỚI: GỌI HÀM CHECK CẢNH BÁO ---
    threading.Thread(target=check_humidity_alarm, args=(humi,)).start()
    # --- HẾT PHẦN MỚI ---

    with lock:
        target = current_recipe["target_soil"]

    print("\n--- Soil Moisture Check ---")
    print(f"Sensor data: Soil={soil}%, Temp={temp}C, Humi={humi}%")
    print(f"Target soil moisture:  {target}%")

    if target == 0:
        print("Decision: Idle stage -> Pump OFF.")
        send_rpc("setPump", {"state": False})
        return jsonify({"status": "idle stage (pump off)"})

    if soil_float >= target:
        print("Decision: Soil moisture is sufficient -> Pump OFF.")
        send_rpc("setPump", {"state": False})
        return jsonify({"status": "pump off"})
    else:
        print("Decision: Soil moisture is too low -> Pump ON.")
        send_rpc("setPump", {"state": True})
        return jsonify({"status": "pump on"})

# ==========================================================
#  API SET GIỜ THỦ CÔNG
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
    
    with lock:
        if current_stage == "Idle_Empty":
            print(f"[MANUAL TIME] Bỏ qua, đang Idle.")
            return jsonify({"status": "idle, no action taken"}), 200
        
        clear_all_jobs()
        
        recipe = current_recipe
        light_hours = recipe.get("light_hours", 12)
        
        print(f"\n--- ⚙️ SET GIỜ THỦ CÔNG: {hour}:00 ---")
        
        if 0 <= hour < light_hours:
            go_to_day(start_hour=hour)
        else:
            go_to_night(is_idle=False, start_hour=hour)

    return jsonify({"status": "ok", "set_hour": hour}), 200

# ==========================================================
#  RUN SERVER (SỬA CHO RENDER.COM)
# ==========================================================
if __name__ == "__main__":
    # Dòng 'scheduler.start()' đã được chuyển lên trên
    # để Gunicorn có thể thấy
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
