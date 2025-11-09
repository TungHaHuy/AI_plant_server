import flask
from flask import Flask, request, jsonify
import requests
import threading
import re 

# --- 1. Cấu hình ThingsBoard (SỬA Ở ĐÂY) ---
TB_HOST = "https://thingsboard.cloud" 
# LƯU Ý: Vẫn dùng DEVICE_TOKEN
DEVICE_TOKEN = "fNsd0L35ywAKakJ979b2" # THAY TOKEN CỦA BẠN

# --- 2. "SÁCH CÔNG THỨC" (Recipes) (SỬA Ở ĐÂY) ---
# Tên "key" PHẢI KHỚP 100% với tên "class" Roboflow
PLANT_RECIPES = {
    "Fruit_and_Ripening": { "target_soil": 50, "light_r": 255, "light_g": 50, "light_b": 0 },
    "Flowering": { "target_soil": 55, "light_r": 255, "light_g": 0, "light_b": 150 },
    "Vegetative": { "target_soil": 60, "light_r": 200, "light_g": 50, "light_b": 200 },
    "Seedling": { "target_soil": 70, "light_r": 150, "light_g": 150, "light_b": 200 },
    "Idle_Empty": { "target_soil": 0, "light_r": 0, "light_g": 0, "light_b": 0 }
}

# --- Biến toàn cục (Không cần sửa) ---
current_stage = "Idle_Empty"
current_recipe = PLANT_RECIPES[current_stage]
lock = threading.Lock()

app = Flask(__name__)

# --- Endpoint 1: Hello World (Không cần sửa) ---
@app.route('/')
def hello():
    return f"AI Plant Server is running. Current stage: {current_stage}"

# --- Endpoint 2: ROBOFLOW WEBHOOK (LOGIC ĐÃ SỬA) ---
@app.route('/roboflow_webhook', methods=['POST'])
def roboflow_webhook():
    data = request.json 
    print("\n--- Received data from Roboflow (RPC Logic) ---")
    print(f"Full payload received: {data}") 
    
    payload_object = data.get("predictions") 
    if not payload_object:
        print(f"ERROR: Payload did not contain 'predictions' key.")
        result = update_stage_internal("Idle_Empty")
        return jsonify(result) # Trả lời ngay

    # 'payload_object' là một DICT { "image": ..., "predictions": [...] }
    detections = payload_object.get('predictions', []) # Lấy list [ ... ]
    if not detections:
        print("No 'detections' list found...")
        result = update_stage_internal("Idle_Empty")
        return jsonify(result) # Trả lời ngay
        
    detected_classes = set()
    for detection in detections:
        if detection.get('confidence', 0) > 0.4:
            detected_classes.add(detection.get('class'))
            
    print(f"Detected classes (conf > 0.4): {detected_classes}")

    # LOGIC ƯU TIÊN (Sửa tên class ở đây)
    new_stage_name = "Idle_Empty"
    if "Seedling" in detected_classes: new_stage_name = "Seedling"
    if "Vegetative" in detected_classes: new_stage_name = "Vegetative"
    if "Flowering" in detected_classes: new_stage_name = "Flowering"
    if "Fruit_and_Ripening" in detected_classes: new_stage_name = "Fruit_and_Ripening"

    print(f"Logic priority selected stage: '{new_stage_name}'")

    result = update_stage_internal(new_stage_name)
    
    # Trả lời 200 OK ngay cho Roboflow
    return jsonify(result), 200

# --- Endpoint 3: process_data (ĐÃ KHÔI PHỤC LOGIC) ---
# Logic BƠM đã quay trở lại server
@app.route('/process_data', methods=['POST'])
def process_data():
    with lock:
        recipe = current_recipe
        
    data = request.json
    current_soil = data.get('soil')
    
    if current_soil is None:
        return jsonify({"error": "Missing 'soil' data"}), 400

    target_soil = recipe.get("target_soil", 0)
    
    # LOGIC BƠM (Chạy trong thread)
    if target_soil == 0:
        print(f"Data: soil={current_soil}%. Recipe: target=0. -> Sending PUMP OFF")
        t = threading.Thread(target=send_thingsboard_rpc, args=("setPump", {"state": False}))
        t.start()
    elif current_soil < target_soil:
        print(f"Data: soil={current_soil}%. Recipe: target={target_soil}%. -> Sending PUMP ON")
        t = threading.Thread(target=send_thingsboard_rpc, args=("setPump", {"state": True}))
        t.start()
    else:
        print(f"Data: soil={current_soil}%. Recipe: target={target_soil}%. -> Sending PUMP OFF")
        t = threading.Thread(target=send_thingsboard_rpc, args=("setPump", {"state": False}))
        t.start()
        
    # Trả lời 200 OK ngay cho ThingsBoard
    return jsonify({"status": "pump logic applied"})

# --- HÀM NỘI BỘ (ĐÃ SỬA VỀ RPC) ---
def update_stage_internal(new_stage):
    global current_stage, current_recipe
    
    if not new_stage: return {"error": "Invalid stage name (empty)"}
    if new_stage not in PLANT_RECIPES:
        print(f"ERROR: Stage '{new_stage}' not found in PLANT_RECIPES!")
        return {"error": f"Stage '{new_stage}' not found in recipes"}
        
    with lock:
        if current_stage == new_stage:
            print(f"Stage '{new_stage}' is already active. No change.")
            return {"status": "success", "message": "Stage already active"}

        current_stage = new_stage
        current_recipe = PLANT_RECIPES[new_stage]
        print(f"--- STAGE CHANGED TO: {current_stage} ---")
        print(f"Recipe loaded: {current_recipe}")

        # TẠO PAYLOAD RPC CHO ĐÈN
        light_params = {
            "r": current_recipe.get("light_r", 0),
            "g": current_recipe.get("light_g", 0),
            "b": current_recipe.get("light_b", 0)
        }
        
        # GỌI HÀM GỬI RPC (TRONG THREAD)
        if light_params["r"] == 0 and light_params["g"] == 0 and light_params["b"] == 0:
            print("Sending RPC (in thread): setLedPower (OFF)")
            t = threading.Thread(target=send_thingsboard_rpc, args=("setLedPower", {"state": False}))
            t.start()
        else:
            print(f"Sending RPC (in thread): setGrowLight ({light_params})")
            t = threading.Thread(target=send_thingsboard_rpc, args=("setGrowLight", light_params))
            t.start()
            
    return {"status": "success", "new_stage": current_stage, "recipe": current_recipe}

# --- HÀM GỬI LỆNH (ĐÃ SỬA VỀ RPC) ---
def send_thingsboard_rpc_mqtt(method, params, token):
    client = mqtt.Client()
    client.username_set(token)
    
    try:
        client.connect(TB_HOST, 1883, 60)
        client.loop_start()
        
        # Payload: Lấy tên method và params ra
        payload = json.dumps({"method": method, "params": params})
        
        # Topic để gửi RPC (remote procedure call)
        topic = f"v1/devices/me/rpc/request/1" # ID request có thể là 1
        
        client.publish(topic, payload, qos=1, retain=False)
        print(f" -> [MQTT] RPC '{method}' sent.")
        
        client.loop_stop()
        client.disconnect()
        return {"status": "success", "protocol": "MQTT"}

    except Exception as e:
        print(f" -> [MQTT] ERROR sending RPC '{method}': {e}")
        return {"status": "error", "message": str(e)}

# --- Chạy server (Đã sửa lỗi "zero") ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=True)
