from flask import Flask, request, jsonify
import requests
import threading
import atexit
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import os # <-- THÊM DÒNG NÀY ĐỂ ĐỌC PORT

# ==========================================================
#  CONFIG (SỬA Ở ĐÂY)
# ==========================================================
TB_API = "https://thingsboard.cloud"

# ID thiết bị (UUID) lấy trong ThingsBoard → Devices → chọn thiết bị → Details
DEVICE_ID = "6cc4a260-bbeb-11f0-8f6e-0181075d8a82"    # <--- SỬA

# JWT Token dài (bạn đã lấy từ API / DevTools)

TB_JWT_TOKEN = "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0eXMyazNAZ21haWwuY29tIiwidXNlcklkIjoiYWU2NjQxODAtYmJlNC0xMWYwLTkxYWQtMDljYTUyZDJkZDkxIiwic2NvcGVzIjpbIlRFTkFOVF9BRE1JTiJdLCJzZXNzaW9uSWQiOiIxNjg4NTExOC1hMGE3LTRmYzktOTcwNS1mMGJjM2NjMWQ3YmEiLCJleHAiOjE3NjI4NTQyODYsImlzcyI6InRoaW5nc2JvYXJkLmNsb3VkIiwiaWF0IjoxNzYyODI1NDg2LCJmaXJzdE5hbWUiOiJUeXMiLCJlbmFibGVkIjp0cnVlLCJpc1B1YmxpYyI6ZmFsc2UsImlzQmlsbGluZ1NlcnZpY2UiOmZhbHNlLCJwcml2YWN5UG9saWN5QWNjZXB0ZWQiOnRydWUsInRlcm1zT2ZVc2VBY2NlcHRlZCI6dHJ1ZSwidGVuYW50SWQiOiJhZTNjZTc5MC1iYmU0LTExZjAtOTFhZC0wOWNhNTJkMmRkOTEiLCJjdXN0b21lcklkIjoiMTM4MTQwMDAtMWRkMi0xMWIyLTgwODAtODA4MDgwODA4MDgwIn0.Ahr9rBZdkFQx7O98WS6WFMObMDxIw0NWfLC9cxUdph2eTphHajAe_6m34JjmaLSFoix3eNkDDgG1RViUmRYduw"

# ==========================================================
#  CÁC CÔNG THỨC TRỒNG CÂY (ĐÃ THAY THẾ)
# ==========================================================
PLANT_RECIPES = {
    # Key gốc: Fruit_and_Ripening -> Dùng data "Fruiting"
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
# BIẾN TOÀN CỤC (ĐÃ THÊM)
# ==========================================================
current_stage = "Idle_Empty"
current_recipe = PLANT_RECIPES[current_stage]
current_day_state = "IDLE" # Trạng thái: "DAY", "NIGHT", "IDLE"
lock = threading.Lock()
scheduler = BackgroundScheduler(daemon=True)

app = Flask(__name__)

# ==========================================================
#  HÀM GỬI RPC (GIỮ NGUYÊN)
# ==========================================================
def send_rpc(method, params):
    url = f"{TB_API}/api/plugins/rpc/oneway/{DEVICE_ID}"
    headers = {"X-Authorization": f"Bearer {TB_JWT_TOKEN}"}
    payload = {"method": method, "params": params}

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=5)
        # Sửa lại Print cho gọn, tránh lỗi unicode
        print(f"[REST RPC] {method} {params} -> {r.status_code}")
    except Exception as e:
        print(f"[REST RPC ERROR] {method} {params} -> {e}")

# ==========================================================
#  HÀM GỬI ATTRIBUTES (HÀM MỚI)
# ==========================================================
def send_attributes(payload):
    url = f"{TB_API}/api/plugins/telemetry/DEVICE/{DEVICE_ID}/attributes/SHARED_SCOPE"
    headers = {"X-Authorization": f"Bearer {TB_JWT_TOKEN}"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=5)
        print(f"[REST ATTR] {payload} -> {r.status_code}")
    except Exception as e:
        print(f"[REST ATTR ERROR] {payload} -> {e}")

# ==========================================================
#  LOGIC "ĐỒNG HỒ SINH HỌC" (Đã sửa Deadlock)
# ==========================================================

def go_to_day(start_hour=0):
    """
    Bật đèn, set ngưỡng ban ngày.
    (ĐÃ XÓA LOCK LỒNG NHAU)
    """
    global current_recipe, current_day_state
    
    # 1. Đảm bảo chúng ta vẫn đang trong 1 stage
    # (KHÔNG CẦN 'with lock:' ở đây nữa)
    if current_stage == "Idle_Empty":
        print("[CLOCK] Bỏ qua go_to_day() vì đang Idle.")
        return

    print(f"\n--- ☀️ PLANT DAYTIME (Start Hour: {start_hour}) ---")
    current_day_state = "DAY"
    recipe = current_recipe 
    
    # 2. Gửi lệnh RPC "Ban ngày"
    r, g, b = recipe["rgb_color"]
    brightness = recipe["brightness"]
    send_rpc("setLedColor", {"r": r, "g": g, "b": b})
    send_rpc("setBrightness", {"brightness": brightness})

    # 3. Gửi Attributes "Ban ngày"
    min_temp_d, max_temp_d = recipe["temp_day"]
    min_humi_d, max_humi_d = recipe["humi_day"]
    attributes_payload = {
        "min_temp": min_temp_d, "max_temp": max_temp_d,
        "min_humi": min_humi_d, "max_humi": max_humi_d,
        "day_cycle": "DAY"
    }
    send_attributes(attributes_payload)

    # 4. Lên lịch đi ngủ
    light_hours = recipe.get("light_hours", 12)
    remaining_hours = light_hours - start_hour
    if remaining_hours <= 0: remaining_hours = 0.01

    run_time = datetime.now() + timedelta(hours=remaining_hours)
    scheduler.add_job(go_to_night, 'date', run_date=run_time, id='night_job')
    print(f"[CLOCK] Đã lên lịch TẮT ĐÈN sau {remaining_hours:.1f} giờ (lúc {run_time.strftime('%H:%M')})")

def go_to_night(is_idle=False, start_hour=None):
    """
    Tắt đèn, set ngưỡng ban đêm.
    (ĐÃ XÓA LOCK LỒNG NHAU)
    """
    global current_recipe, current_day_state
    
    # (KHÔNG CẦN 'with lock:' ở đây nữa)
    recipe = current_recipe 
    
    if is_idle:
        print(f"\n--- 💤 PLANT IDLE ---")
        current_day_state = "IDLE"
    else:
        print(f"\n--- 🌙 PLANT NIGHTTIME (Start Hour: {start_hour}) ---")
        current_day_state = "NIGHT"

    # 2. Gửi lệnh RPC "Ban đêm" / "Idle"
    send_rpc("setLedPower", {"state": False}) # Tắt đèn

    # 3. Gửi Attributes "Ban đêm" / "Idle"
    min_temp_n, max_temp_n = recipe["temp_night"]
    min_humi_n, max_humi_n = recipe["humi_night"]
    attributes_payload = {
        "min_temp": min_temp_n, "max_temp": max_temp_n,
        "min_humi": min_humi_n, "max_humi": max_humi_n,
        "day_cycle": "NIGHT" if not is_idle else "IDLE"
    }
    send_attributes(attributes_payload)

    # 4. Lên lịch thức dậy
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
    """Xóa mọi lịch trình đã đặt."""
    print("[CLOCK] Hủy tất cả lịch trình (day_job/night_job).")
    try:
        if scheduler.get_job('day_job'):
            scheduler.remove_job('day_job')
        if scheduler.get_job('night_job'):
            scheduler.remove_job('night_job')
    except Exception as e:
        print(f"[CLOCK ERROR] Lỗi khi xóa job: {e}")

# ==========================================================
#  CẬP NHẬT GIAI ĐOẠN PHÁT TRIỂN (ĐÃ THAY THẾ)
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
#  WEB UI CHECK (ĐÃ CẬP NHẬT)
# ==========================================================
@app.route("/")
def home():
    # Cập nhật để hiển thị trạng thái Day/Night
    return f"✅ AI Plant Server is running — Current stage: {current_stage} ({current_day_state})"

# ==========================================================
#  HÀM WORKER CHO WEBHOOK (HÀM MỚI)
# ==========================================================
def process_webhook_async(new_stage):
    """
    Hàm worker này chạy trong một thread riêng
    để thực hiện công việc nặng (update_stage_internal)
    mà không làm Roboflow bị timeout.
    """
    print(f"[ASYNC WORKER] Bắt đầu xử lý cho stage: {new_stage}")
    # Gọi hàm gốc (giờ đã an toàn vì đang ở thread riêng)
    update_stage_internal(new_stage)
    print(f"[ASYNC WORKER] Xử lý xong cho stage: {new_stage}")

# ==========================================================
#  WEBHOOK NHẬN KẾT QUẢ TỪ ROBOFLOW (ĐÃ THAY THẾ)
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
        best_prediction_class = "Idle_Empty"
        max_confidence = 0.0
        
        for p in predictions_list:
            conf = p.get("confidence", 0)
            if conf > max_confidence:
                max_confidence = conf
                best_prediction_class = p.get("class", "Idle_Empty")

        if max_confidence < 0.4:
            print(f"Confidence below 40% ({max_confidence}). Setting to Idle.")
            best_prediction_class = "Idle_Empty"

        print(f"Best detection: {best_prediction_class} (Conf: {max_confidence})")
        
        new_stage = "Idle_Empty"
        
        if best_prediction_class in PLANT_RECIPES:
            new_stage = best_prediction_class
        else:
            if "Seedling" in best_prediction_class: new_stage = "Seedling"
            elif "Vegetative" in best_prediction_class: new_stage = "Vegetative"
            elif "Flowering" in best_prediction_class: new_stage = "Flowering"
            elif "Fruit_and_Ripening" in best_prediction_class: new_stage = "Fruit_and_Ripening"
            elif "Fruiting" in best_prediction_class: new_stage = "Fruit_and_Ripening"

    # --- SỬA LOGIC: CHẠY TRONG THREAD MỚI ---
    worker_thread = threading.Thread(
        target=process_webhook_async,
        args=(new_stage,)
    )
    worker_thread.start()
    
    # Trả lời "OK" ngay lập tức cho Roboflow
    print("[WEBHOOK] Gửi 200 OK cho Roboflow. Xử lý trong nền...")
    return jsonify({"status": "received, processing in background"}), 200

# ==========================================================
#  PROCESS SENSOR DATA (ĐÃ CẬP NHẬT)
# ==========================================================
@app.route("/process_data", methods=["POST"])
def process_data():
    data = request.json
    
    # Cập nhật: Lấy tất cả data
    soil = data.get("soil")
    temp = data.get("temperature")
    humi = data.get("humidity")

    if soil is None:
        return jsonify({"error": "Missing 'soil'"}), 400

    try:
        soil_float = float(soil)
    except (ValueError, TypeError):
        return jsonify({"error": f"Invalid 'soil' value: {soil}"}), 400

    # Khóa lock khi đọc current_recipe
    with lock:
        target = current_recipe["target_soil"]

    print("\n--- Soil Moisture Check ---")
    # Cập nhật: In đầy đủ
    print(f"Sensor data: Soil={soil}%, Temp={temp}C, Humi={humi}%")
    print(f"Target soil moisture:  {target}%")

    # Logic tưới (Giữ nguyên)
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
#  API SET GIỜ THỦ CÔNG (API MỚI)
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
    # Khởi động scheduler
    scheduler.start()
    print("Scheduler đã khởi động...")
    # Đảm bảo scheduler tắt khi app tắt
    atexit.register(lambda: scheduler.shutdown())
    
    # Lấy port từ biến môi trường của Render, nếu không có thì dùng 7860
    port = int(os.environ.get("PORT", 7860))
    
    # Tắt debug=True vì nó xung đột với scheduler
    app.run(host="0.0.0.0", port=port, debug=False)
