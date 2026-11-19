# 혼잡도 수집 (EC2 cron, 매시 정각 / 한국 시간 9~24시)
# 75개 장소 좌표로 SKT Tmap 교통 API를 호출해 도로 혼잡도(1~4)와 속도를 받아
# PopulationFlow 테이블에 저장합니다.
# 앱키 2개를 홀수/짝수 시간에 번갈아 사용해 일일 호출 한도를 나눴습니다.
import os
import requests
import json
import time
from datetime import datetime
import pytz
import pymysql

# ✅ 앱 키 2개 (홀수 시간: 키1 / 짝수 시간: 키2)
API_KEYS = [
    os.environ["TMAP_APP_KEY_1"],
    os.environ["TMAP_APP_KEY_2"],
]

API_URL = 'https://apis.openapi.sk.com/tmap/traffic'

# ✅ RDS 연결 정보
DB_HOST = os.environ["DB_HOST"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_NAME = 'in9cow'

# ✅ 현재 시간 (한국 기준)
kst = pytz.timezone('Asia/Seoul')
now_kst = datetime.now(kst)
timestamp = now_kst.strftime('%Y-%m-%d %H:%M:%S')

# ✅ 홀/짝 시간에 따라 앱키 선택
def select_api_key(hour):
    return API_KEYS[0] if hour % 2 == 1 else API_KEYS[1]

API_KEY = select_api_key(now_kst.hour)
print(f"🕐 현재 시각: {now_kst.hour}시 → 사용 앱키: {API_KEY[:6]}...")

# ✅ 좌표 JSON 불러오기
with open('place_coordinates.json', 'r', encoding='utf-8') as f:
    place_coords = json.load(f)

# ✅ DB 연결
conn = pymysql.connect(
    host=DB_HOST,
    user=DB_USER,
    password=DB_PASSWORD,
    db=DB_NAME,
    charset='utf8mb4'
)
cursor = conn.cursor()

# ✅ API 호출 함수
def call_api(lat, lon):
    params = {
        'version': '1',
        'reqCoordType': 'WGS84GEO',
        'resCoordType': 'EPSG3857',
        'zoomLevel': '15',
        'trafficType': 'AUTO',
        'centerLat': lat,
        'centerLon': lon,
        'appKey': API_KEY
    }

    headers = {'accept': 'application/json'}
    response = requests.get(API_URL, headers=headers, params=params, timeout=3)
    return response.json()

# ✅ 로그 기록 (cron 실행 확인용)
with open("/home/ec2-user/app_debug.log", "a") as f:
    f.write("✅ cron 실행됨 at {}\n".format(datetime.now(kst)))

# ✅ 각 장소별 혼잡도 수집 및 DB 저장
for name, coord in place_coords.items():
    lat = coord['lat']
    lon = coord['lon']

    try:
        data = call_api(lat, lon)
        features = data.get('features', [])

        if features:
            props = features[0]['properties']
            congestion = props.get('congestion', None)
            speed = props.get('speed', None)
        else:
            congestion = None
            speed = None

        sql = """
            INSERT INTO PopulationFlow (location_id, congestion, speed, timestamp)
            SELECT location_id, %s, %s, %s
            FROM Locations WHERE location_name = %s
        """
        cursor.execute(sql, (congestion, speed, timestamp, name))
        print(f"✅ 저장 완료: {name} / 혼잡도={congestion}, 속도={speed}")

    except Exception as e:
        print(f"❌ 오류 ({name}): {e}")

    time.sleep(0.2)

# ✅ DB 종료
conn.commit()
cursor.close()
conn.close()

print("✅ 전체 완료: RDS 저장까지 마무리했습니다.")
