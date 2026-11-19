# 예측 갱신 (EC2 cron, 매시 3분 / 수집 직후)
# 장소마다 방금 수집된 관측값과 해당 요일·시간 칸의 기존 예측값을 GPT-4o에 전달해 예측을 갱신합니다.
# 두 값의 차이가 크면 관측값 쪽으로, 작으면 중간값으로 조정하도록 프롬프트에 규칙을 두었습니다.
# GPT 응답에서 숫자만 추출하고, 실패한 장소는 건너뛰고 로그에 남깁니다.
import os
import pymysql
import openai
from datetime import datetime
import pytz
import re

# OpenAI 클라이언트 설정
client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# MySQL 연결
def get_connection():
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        db='in9cow',
        charset='utf8',
        cursorclass=pymysql.cursors.DictCursor
    )

# GPT 프롬프트 생성
def build_prompt(prev_ai, latest_real):
    return f"""
너는 시간대별 혼잡도를 예측하는 AI야.
지금까지의 예측값(정수)과 방금 들어온 실제 관측값(정수)을 보고, 다음 시간대의 혼잡도를 정수로 예측해야 해.

규칙은 다음과 같아:
- 두 값의 차이가 크면 (2 이상이면), 새로운 관측값이 추세 전환일 수 있으니 그 쪽에 비중을 둬.
- 두 값의 차이가 작으면, 관측값 쪽에 더 가중치를 두고 반영하지만 예측값과 중간값 정도로 조정해.
- 예측값은 항상 **정수**로 출력하고, 특별한 이유 없으면 **반올림**을 사용해.
- 출력은 숫자 하나만, 예측값으로 줘.

예측: {prev_ai}, 관측: {latest_real}
"""

# 요일 + 현재 시각 (KST 기준)
def get_korean_day():
    now = datetime.now(pytz.timezone('Asia/Seoul'))
    day_num = now.isoweekday()  # 월=1 ~ 일=7
    kor_day_map = {
        1: '월', 2: '화', 3: '수',
        4: '목', 5: '금', 6: '토', 7: '일'
    }
    return kor_day_map[day_num], now.hour

# GPT 응답에서 숫자만 추출
def extract_prediction(content):
    match = re.search(r'\d+', content)
    return int(match.group()) if match else None

# 메인 실행 함수
def main():
    conn = get_connection()
    now = datetime.now(pytz.timezone('Asia/Seoul'))
    day_kor, hour = get_korean_day()

    try:
        with conn.cursor() as cur:
            # 모든 장소 ID 조회
            cur.execute("SELECT location_id FROM Locations")
            locations = cur.fetchall()

            for loc in locations:
                location_id = loc['location_id']

                # 최신 관측값 조회
                cur.execute("""
                    SELECT congestion FROM PopulationFlow
                    WHERE location_id = %s
                    ORDER BY timestamp DESC LIMIT 1
                """, (location_id,))
                real = cur.fetchone()
                if not real:
                    continue

                # 가장 최근 예측값 조회
                cur.execute("""
                    SELECT ai_congestion FROM prediction
                    WHERE location_id = %s AND day = %s AND hour = %s
                """, (location_id, day_kor, hour))
                prev = cur.fetchone()
                prev_ai = prev['ai_congestion'] if prev else real['congestion']

                # GPT 프롬프트 생성 및 호출
                prompt = build_prompt(prev_ai, real['congestion'])

                try:
                    response = client.chat.completions.create(
                        model='gpt-4o',
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0
                    )
                    raw_text = response.choices[0].message.content.strip()
                    predicted = extract_prediction(raw_text)

                    if predicted is not None:
                        # DB 저장 또는 갱신
                        cur.execute("""
                            INSERT INTO prediction (location_id, day, hour, ai_congestion)
                            VALUES (%s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE ai_congestion = %s
                        """, (location_id, day_kor, hour, predicted, predicted))
                        conn.commit()

                        log = f"[{datetime.now()}] ✅ Updated: loc={location_id}, day={day_kor}, hour={hour}, value={predicted}\n"
                    else:
                        log = f"[{datetime.now()}] ❗예측 실패 (숫자 없음): loc={location_id}, hour={hour}, GPT 응답: {raw_text}\n"

                except Exception as e:
                    log = f"[{datetime.now()}] ⚠️ GPT 오류: loc={location_id}, hour={hour}, error={e}\n"

                # 로그 파일에 기록
                with open("/home/ec2-user/update.log", "a") as f:
                    f.write(log)

    finally:
        conn.close()

if __name__ == '__main__':
    main()
