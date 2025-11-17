from flask import Flask, request, jsonify
import requests
import threading
import atexit
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import os 
import base64 
import requests 

# ==========================================================
#  CONFIG (SỬA Ở ĐÂY)
# ==========================================================
TB_API = "https://thingsboard.cloud"

# ID thiết bị (UUID) lấy trong ThingsBoard → Devices → chọn thiết bị → Details
DEVICE_ID = "6cc4a260-bbeb-11f0-8f6e-0181075d8a82"    # <--- SỬA
DEVICE_TOKEN = "fNsd0L35ywAKakJ979b2"
ROBOFLOW_API_URL = "https://serverless.roboflow.com/tunghahuy/workflows/custom-workflow"
ROBOFLOW_API_KEY = "YY5sAfysi1GpnWgkVPfF"


# JWT Token dài (bạn đã lấy từ API / DevTools)
TB_JWT_TOKEN = "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0eXMyazNAZ21haWwuY29tIiwidXNlcklkIjoiYWU2NjQxODAtYmJlNC0xMWYwLTkxYWQtMDljYTUyZDJkZDkxIiwic2NvcGVzIjpbIlRFTkFOVF9BRE1JTiJdLCJzZXNzaW9uSWQiOiIxNjg4NTExOC1hMGE3LTRmYzktOTcwNS1mMGJjM2NjMWQ3YmEiLCJleHAiOjE3NjI4NTQyODYsImlzcyI6InRoaW5nc2JvYXJkLmNsb3VkIiwiaWF0IjoxNzYyODI1NDg2LCJmaXJzdE5hbWUiOiJUeXMiLCJlbmFibGVkIjp0cnVlLCJpc1B1YmxpYyI6ZmFsc2UsImlzQmlsbGluZ1NlcnZpY2UiOmZhbHNlLCJwcml2YWN5UG9saWN5QWNjZXB0ZWQiOnRydWUsInRlcm1zT2ZVc2VBY2NlcHRlZCI6dHJ1ZSwidGVuYW50SWQiOiJhZTNjZTc5MC1iYmU0LTExZjAtOTFhZC0wOWNhNTJkMmRkOTEiLCJjdXN0b21lcklkIjoiMTM4MTQwMDAtMWRkMi0xMWIyLTgwODAtODA4MDgwODA4MDgwIn0.Ahr9rBZdkFQx7O98WS6WFMObMDxIw0NWfLC9cxUdph2eTphHajAe_6m34JjmaLSFoix3eNkDDGgG1RViUmRYduw"

last_pump_state = None    # None / True / False
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
    "Seeding": {
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
is_manual_mode = False # <-- BIẾN CÔNG TẮC MANUAL
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
    url = f"{TB_API}/api/v1/{DEVICE_TOKEN}/attributes"
    try:
        r = requests.post(url, json=payload, timeout=3)
        print(f"[ATTR] {payload} -> {r.status_code}")
    except Exception as e:
        print(f"[ATTR ERROR] {payload} -> {e}")

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
#  LOGIC "ĐỒNG HỒ SINH HỌC" (ĐÃ SỬA CHÍNH XÁC)
# ==========================================================
def go_to_day(start_hour=0):
    global current_recipe, current_day_state, is_manual_mode 
    
    if current_stage == "Idle_Empty":
        print("[CLOCK] Bỏ qua go_to_day() vì đang Idle.")
        return

    print(f"\n--- ☀️ PLANT DAYTIME (Start Hour: {start_hour}) ---")
    current_day_state = "DAY"
    recipe = current_recipe 

    # --- CỔNG CHẶN RPC (CHỈ CHẶN GỬI LỆNH) ---
    with lock:
        if is_manual_mode:
            print("[CLOCK] Đang ở Manual Mode, **bỏ qua gửi lệnh RPC đèn**.")
        else:
            # Chỉ gửi lệnh nếu KHÔNG ở Manual Mode
            print("[CLOCK] Gửi lệnh RPC cho ban ngày...")
            r, g, b = recipe["rgb_color"]
            brightness = recipe["brightness"]
            send_rpc("setLedColor", {"ledR": r, "ledG": g, "ledB": b}) 
            send_rpc("setBrightness", {"brightness": brightness})

        # LUÔN GỬI ATTRIBUTES (để UI đồng bộ, đây không phải RPC điều khiển)
        min_temp_d, max_temp_d = recipe["temp_day"]
        min_humi_d, max_humi_d = recipe["humi_day"]
        attributes_payload = {
            "min_temp": min_temp_d, "max_temp": max_temp_d,
            "min_humi": min_humi_d, "max_humi": max_humi_d,
            "day_cycle": "DAY"
        }
        send_attributes(attributes_payload)
    # --- KẾT THÚC CỔNG CHẶN RPC ---

    light_hours = recipe.get("light_hours", 12)
    remaining_hours = light_hours - hour
    if remaining_hours <= 0: remaining_hours = 0.01

    # LỊCH HẸN VẪN PHẢI CHẠY (Giữ nguyên đồng hồ)
    try:
        if scheduler.get_job('night_job'):
             scheduler.remove_job('night_job')
    except: pass
    
    run_time = datetime.now() + timedelta(hours=remaining_hours)
    # CHẠY LẠI CHÍNH XÁC LÚC run_time ĐỂ ĐẶT LẠI LỊCH (ĐỒNG HỒ SINH HỌC VẪN PHẢI CHẠY)
    scheduler.add_job(go_to_night, 'date', run_date=run_time, id='night_job') 
    print(f"[CLOCK] Đã lên lịch TẮT ĐÈN sau {remaining_hours:.1f} giờ (lúc {run_time.strftime('%H:%M')})")

def go_to_night(is_idle=False, start_hour=None):
    global current_recipe, current_day_state, is_manual_mode 
    
    recipe = current_recipe 
    
    if is_idle:
        print(f"\n--- 💤 PLANT IDLE ---")
        current_day_state = "IDLE"
    else:
        print(f"\n--- 🌙 PLANT NIGHTTIME (Start Hour: {start_hour}) ---")
        current_day_state = "NIGHT"
        
    # --- CỔNG CHẶN RPC (CHỈ CHẶN GỬI LỆNH) ---
    with lock:
        if is_manual_mode:
            print("[CLOCK] Đang ở Manual Mode, **bỏ qua gửi lệnh RPC đèn/bơm**.")
        else:
            # Chỉ gửi lệnh nếu KHÔNG ở Manual Mode
            print("[CLOCK] Gửi lệnh RPC cho ban đêm...")
            send_rpc("setPump", {"state": False}) # Tắt bơm
            send_rpc("setLedPower", {"state": False}) # Tắt đèn

        # LUÔN GỬI ATTRIBUTES (để UI đồng bộ)
        min_temp_n, max_temp_n = recipe["temp_night"]
        min_humi_n, max_humi_n = recipe["humi_night"]
        attributes_payload = {
            "min_temp": min_temp_n, "max_temp": max_temp_n,
            "min_humi": min_humi_n, "max_humi": max_humi_n,
            "day_cycle": "NIGHT" if not is_idle else "IDLE"
        }
        send_attributes(attributes_payload)
    # --- KẾT THÚC CỔNG CHẶN RPC ---

    if not is_idle:
        light_hours = recipe.get("light_hours", 12)
        
        if start_hour is not None:
            remaining_hours = 24 - start_hour
        else:
            remaining_hours = 24 - light_hours
            
        if remaining_hours <= 0: remaining_hours = 8
        
        # LỊCH HẸN VẪN PHẢI CHẠY
        try:
            if scheduler.get_job('day_job'):
                scheduler.remove_job('day_job')
        except: pass

        run_time = datetime.now() + timedelta(hours=remaining_hours)
        # CHẠY LẠI CHÍNH XÁC LÚC run_time ĐỂ ĐẶT LẠI LỊCH (ĐỒNG HỒ SINH HỌC VẪN PHẢI CHẠY)
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
#  CẬP NHẬT GIAI ĐOẠN PHÁT TRIỂN (VẪN CHẠY)
# ==========================================================
def update_stage_internal(new_stage):
    global current_stage, current_recipe
    global last_pump_state

    if new_stage not in PLANT_RECIPES:
        print(f"Lỗi: Không tìm thấy stage '{new_stage}' trong PLANT_RECIPES.")
        return {"error": f"Stage '{new_stage}' not found"}

    with lock:
        # Bỏ check 'no change' để cho phép sync khi tắt manual
        
        print(f"\n--- STAGE UPDATE: {current_stage} → {new_stage} ---")
        current_stage = new_stage
        current_recipe = PLANT_RECIPES[current_stage]

        last_pump_state = None  # Reset so pump logic bắt đầu lại đúng

        clear_all_jobs()

        if new_stage == "Idle_Empty":
            go_to_night(is_idle=True)
        else:
            # AI thay đổi stage -> Bắt đầu lại chu kỳ từ Day (start_hour=0)
            go_to_day(start_hour=0) 

    return {"status": "ok", "stage": current_stage}

# ==========================================================
#  WEB UI CHECK
# ==========================================================
@app.route("/")
def home():
    return f"✅ AI Plant Server is running — Current stage: {current_stage} ({current_day_state}) — Manual: {is_manual_mode}"

# ==========================================================
#  WEBHOOK NHẬN KẾT QUẢ TỪ ROBOFLOW (VẪN CHẠY)
# ==========================================================
@app.route("/roboflow_webhook", methods=["POST"])
def roboflow_webhook():
    
    # *** ĐÃ XÓA CỔNG CHẶN MANUAL MODE Ở ĐÂY ***
    # Cho phép Roboflow luôn chạy và cập nhật stage/công thức

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
            if "Seeding" in detected_classes: new_stage = "Seeding"
            if "Vegetative" in detected_classes: new_stage = "Vegetative"
            if "Flowering" in detected_classes: new_stage = "Flowering"
            if "Fruit_and_Ripening" in detected_classes: new_stage = "Fruit_and_Ripening"
            if "Fruiting" in detected_classes: new_stage = "Fruit_and_Ripening" 

    print(f"[WEBHOOK] Giai đoạn ưu tiên cuối cùng: {new_stage}")

    print("[WEBHOOK] Đang xử lý đồng bộ (chặn Roboflow)...")
    # Luôn gọi hàm update_stage_internal
    json_response = update_stage_internal(new_stage) 
    
    print("[WEBHOOK] Xử lý đồng bộ XONG. Gửi 200 OK.")
    return jsonify(json_response), 200

# ==========================================================
#  ENDPOINT MỚI: NHẬN ẢNH TỪ THINGSBOARD -> GỬI TỚI ROBOFLOW (VẪN CHẠY)
# ==========================================================
@app.route("/process_photo_from_thingsboard", methods=["POST"])
def process_photo_from_thingsboard():     
    # *** ĐÃ XÓA CỔNG CHẶN MANUAL MODE Ở ĐÂY ***
    # Cho phép chụp ảnh và gửi đi luôn chạy.

    data = request.json      
    if not data:
        print("[PROCESS PHOTO] Lỗi: Không nhận được payload.")
        return jsonify({"status": "error", "message": "Missing payload"}), 400

    b64_image = data.get("photo")
    
    if not b64_image and "msg" in data and isinstance(data["msg"], dict):
        b64_image = data["msg"].get("photo")
        
    if not b64_image and "values" in data and isinstance(data["values"], dict):
        b64_image = data["values"].get("photo")

    if not b64_image:
        print(f"[PROCESS PHOTO] Lỗi: Không tìm thấy key 'photo' trong payload. Dữ liệu nhận được: {data}")
        return jsonify({"status": "error", "message": "Missing 'photo' key in payload"}), 400

    if b64_image.startswith("data:image"):
        b64_image = b64_image.split(',')[-1]
        print("[PROCESS PHOTO] Đã loại bỏ tiền tố data URI.")

    roboflow_payload = {
        "api_key": ROBOFLOW_API_KEY,
        "inputs": {
            "image": {
                "type": "base64",
                "value": b64_image
            }
        }
    }

    try:
        print(f"[PROCESS PHOTO] Đang gửi ảnh (Base64) tới Roboflow Workflow...")
        
        roboflow_response = requests.post(
            ROBOFLOW_API_URL,
            json=roboflow_payload, # Gửi dưới dạng JSON
            headers={"Content-Type": "application/json"},
            timeout=20 
        )
        
        roboflow_response.raise_for_status() 
        
        print(f"[PROCESS PHOTO] Roboflow phản hồi: {roboflow_response.status_code}")
        return jsonify({"status": "ok", "message": "Image sent to Roboflow"}), 200

    except requests.exceptions.RequestException as e:
        print(f"[PROCESS PHOTO] Lỗi khi gửi ảnh tới Roboflow: {e}")
        return jsonify({"status": "error", "message": f"Failed to send image to Roboflow: {e}"}), 500
    except Exception as e:
        print(f"[PROCESS PHOTO] Lỗi không xác định: {e}")
        return jsonify({"status": "error", "message": f"An unexpected error occurred: {e}"}), 500

# ==========================================================
#  PROCESS SENSOR DATA (ĐÃ SỬA CHÍNH XÁC)
# ==========================================================
@app.route("/process_data", methods=["POST"])
def process_data():
    global last_pump_state,is_manual_mode  

    # *** ĐÃ XÓA CỔNG CHẶN MANUAL MODE Ở ĐÂY ***
    # Cho phép logic xử lý sensor và cảnh báo (check_humidity_alarm) luôn chạy.

    data = request.json
    
    soil = data.get("soil")
    temp = data.get("temperature")
    humi = data.get("humidity") 

    if soil is None:
        return jsonify({"error": "Missing 'soil'"}), 400

    try:
        soil = float(soil)
        temp = float(temp)
        humi = float(humi)
    except:
        return jsonify({"error": "Invalid sensor data"}), 400

    # Cho phép cảnh báo độ ẩm chạy song song (LUÔN CHẠY)
    threading.Thread(target=check_humidity_alarm, args=(humi,)).start()

    with lock:
        recipe = current_recipe
        day_state = current_day_state

    print("\n--- Soil Moisture Check ---")
    print(f"Soil={soil}%, Temp={temp}C, Humi={humi}%")

    # ====== LẤY NGƯỠNG ======
    if day_state == "DAY":
        min_humi, max_humi = recipe["humi_day"]
        min_temp, max_temp = recipe["temp_day"]
    else:
        min_humi, max_humi = recipe["humi_night"]
        min_temp, max_temp = recipe["temp_night"]

    target = recipe["target_soil"]

    # ====== TÍNH -1 / 0 / 1 ======
    soil_state = -1 if soil < target else (1 if soil > target else 0)
    humi_state = -1 if humi < min_humi else (1 if humi > max_humi else 0)
    temp_state = -1 if temp < min_temp else (1 if temp > max_temp else 0)

    # ====== GỬI 3 TRẠNG THÁI LÊN THINGSBOARD (LUÔN GỬI) ======
    send_attributes({
        "soil_state": soil_state,
        "humi_state": humi_state,
        "temp_state": temp_state
    })

    # ====== QUYẾT ĐỊNH BƠM (CÓ CỔNG CHẶN Ở ĐÂY) ======
    with lock:
        if is_manual_mode:
            print("[PUMP] Đang ở Manual Mode, **bỏ qua gửi lệnh RPC bơm**.")
            return jsonify({"status": "skipped", "reason": "manual mode"})

    # Chỉ chạy logic bơm nếu KHÔNG ở manual mode (Logic này được kích hoạt)
    if target == 0:
        desired_state = False
    
        if last_pump_state != desired_state:
            print(f"[PUMP] Idle mode → state changed → sending RPC: {desired_state}")
            send_rpc("setPump", {"state": desired_state})
            last_pump_state = desired_state
        else:
            print(f"[PUMP] Idle mode → state unchanged ({desired_state}) → no RPC sent")
    
        return jsonify({"status": "idle (pump off)"})

    
    desired_state = (soil_state == -1)  # True = ON, False = OFF
    
    if last_pump_state != desired_state:
        print(f"[PUMP] State changed → sending RPC: {desired_state}")
        send_rpc("setPump", {"state": desired_state})
        last_pump_state = desired_state
    else:
        print(f"[PUMP] State unchanged ({desired_state}) → no RPC sent")
    
    return jsonify({"status": "pump on" if desired_state else "pump off"})
# ==========================================================
#  API SET GIỜ THỦ CÔNG (VẪN CHẠY)
# ==========================================================
@app.route("/set_manual_time", methods=["POST"])
def set_manual_time():
    
    # *** ĐÃ XÓA CỔNG CHẶN MANUAL MODE Ở ĐÂY ***
    # Cho phép set giờ thủ công luôn chạy để đặt lại đồng hồ sinh học.
    
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
    
    # Logic Set Giờ (VẪN CHẠY)
    with lock:
        # Không cần check manual mode ở đây, vì cổng chặn nằm trong go_to_day/go_to_night
            
        if current_stage == "Idle_Empty":
            print(f"[MANUAL TIME] Bỏ qua, đang Idle.")
            return jsonify({"status": "idle, no action taken"}), 200
        
        clear_all_jobs()
        
        recipe = current_recipe
        light_hours = recipe.get("light_hours", 12)
        
        print(f"\n--- ⚙️ SET GIỜ THỦ CÔNG: {hour}:00 ---")
        
        # Chỉ gọi go_to_day/go_to_night để ĐẶT LẠI LỊCH (Scheduler)
        if 0 <= hour < light_hours:
            go_to_day(start_hour=hour)
        else:
            go_to_night(is_idle=False, start_hour=hour)

    return jsonify({"status": "ok", "set_hour": hour}), 200

# ==========================================================
#  ENDPOINT MỚI: NHẬN LỆNH MANUAL MODE TỪ THINGSBOARD (GIỮ NGUYÊN)
# ==========================================================
@app.route("/set_manual_mode", methods=["POST"])
def set_manual_mode():
    global is_manual_mode, current_stage, current_recipe, current_day_state
    global last_pump_state
    
    data = request.json # Payload dự kiến: {"current_mode": true/false}

    try:
        new_mode_value = data.get("current_mode") 
        if new_mode_value is None:
             print("[MANUAL MODE] Lỗi: /set_manual_mode không nhận được 'current_mode' key.")
             return jsonify({"error": "Missing 'current_mode' key"}), 400

        # Chuyển đổi sang boolean
        if isinstance(new_mode_value, str):
            new_mode_bool = new_mode_value.lower() == 'true'
        else:
            new_mode_bool = bool(new_mode_value)

        with lock:
            if is_manual_mode == new_mode_bool:
                print(f"[MANUAL MODE] Chế độ không đổi: {is_manual_mode}")
                return jsonify({"status": "no_change"})

            # === THAY ĐỔI TRẠNG THÁI ===
            is_manual_mode = new_mode_bool
            print(f"\n--- ⚙️ CHUYỂN CHẾ ĐỘ MANUAL: {is_manual_mode} ---")
            
            if is_manual_mode:
                # BẬT Manual: Không làm gì cả. 
                # Các "cổng chặn" sẽ tự lo việc chặn auto.
                # Đồng hồ và AI vẫn chạy ngầm.
                print("[MANUAL MODE] Đã bật. Đồng hồ và AI vẫn chạy ngầm.")
            
            else:
                # TẮT Manual (QUAY VỀ AUTO)
                # Đây là phần "đồng bộ" lại trạng thái
                
                print("[MANUAL MODE] Đã tắt. Đồng bộ lại trạng thái Auto...")
                
                # 1. Đồng bộ đèn (Gửi lại lệnh)
                if current_day_state == "DAY":
                    print("[MANUAL MODE] Đồng bộ: Ban ngày -> Bật đèn.")
                    recipe = current_recipe
                    r, g, b = recipe["rgb_color"]
                    brightness = recipe["brightness"]
                    send_rpc("setLedColor", {"ledR": r, "ledG": g, "ledB": b}) 
                    send_rpc("setBrightness", {"brightness": brightness})
                elif current_day_state == "NIGHT":
                    print("[MANUAL MODE] Đồng bộ: Ban đêm -> Tắt đèn.")
                    send_rpc("setLedPower", {"state": False})
                else: # IDLE
                    print("[MANUAL MODE] Đồng bộ: Idle -> Tắt đèn/bơm.")
                    send_rpc("setLedPower", {"state": False})
                    send_rpc("setPump", {"state": False})

                # 2. Đồng bộ bơm (Check lại độ ẩm đất)
                # Bằng cách reset last_pump_state, lần check cảm biến tới
                # sẽ bắt buộc phải chạy logic bơm.
                print("[MANUAL MODE] Đồng bộ: Reset logic bơm.")
                last_pump_state = None 

        return jsonify({"status": "ok", "manual_mode_is_on": is_manual_mode}), 200

    except Exception as e:
        print(f"[MANUAL MODE] Lỗi: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================================================
#  RUN SERVER (SỬA CHO RENDER.COM)
# ==========================================================
if __name__ == "__main__":
    # Dòng 'scheduler.start()' đã được chuyển lên trên
    # để Gunicorn có thể thấy
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
