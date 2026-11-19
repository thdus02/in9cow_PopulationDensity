# 예측표 초기 채우기 (최초 1회 실행)
# prediction 테이블의 모든 칸(장소 × 요일 × 9~24시)에 대해 같은 요일·시간의 최근 관측 기록 5개를
# GPT-4o에 전달하고, 예측 혼잡도(1~4)를 받아 저장합니다.
# 수집 데이터는 자정을 0시로 기록하므로, 24시 칸은 0시 기록으로 조회합니다.
import os
import openai
import pymysql

client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

def get_connection():
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        db="in9cow",
        charset="utf8",
        cursorclass=pymysql.cursors.DictCursor
    )

def build_prompt(location_id, day, hour):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            query_hour = 0 if hour == 24 else hour
            cur.execute("""
                SELECT congestion
                FROM PopulationFlow
                WHERE location_id = %s
                AND HOUR(timestamp) = %s
                AND DAYOFWEEK(timestamp) = %s
                ORDER BY timestamp DESC
                LIMIT 5
            """, (location_id, query_hour, ['월','화','수','목','금','토','일'].index(day) + 1))
            rows = cur.fetchall()
            if rows:
                history = ", ".join(str(r['congestion']) for r in rows)
                return (f"{day} {hour}시 혼잡도 기록: {history}"
                        f"\n→ 다음 혼잡도는? 혼잡도는 1~4까지의 정수로만 대답해주세요. 아무 말 없이 숫자만 알려줘.")
            else:
                return None
    finally:
        conn.close()

def get_prediction_from_gpt(prompt):
    if prompt is None:
        return None
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "너는 도심 교통 및 인구 혼잡도 예측 전문가야. 반드시 숫자만 응답해."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1,  # 응답으로 생성되는 최대 토큰 수(단어+기호+숫자) 를 제한
            response_format="text",  # strict 모드를 사용하는 경우 json이지만, text만 받을 땐 text 유지
            strict=True
       )
        result = response.choices[0].message.content.strip()
        return int(''.join(filter(str.isdigit, result)))
    except Exception as e:
        print(f"GPT error: {e}")
        return None

def save_prediction(location_id, day, hour, ai_congestion):
    if ai_congestion is None:
        print(f"예측 실패: {location_id} / {day} / {hour}")
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE prediction
                SET ai_congestion = %s
                WHERE location_id = %s AND day = %s AND hour = %s
            """, (ai_congestion, location_id, day, hour))
            conn.commit()
            print(f"저장됨: {location_id} / {day} {hour}시 → {ai_congestion}")
    finally:
        conn.close()

def run_batch_prediction():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT location_id, day, hour FROM prediction")
            targets = cur.fetchall()
    finally:
        conn.close()

    for row in targets:
        location_id = row['location_id']
        day = row['day']
        hour = row['hour']
        prompt = build_prompt(location_id, day, hour)
        ai_congestion = get_prediction_from_gpt(prompt)
        save_prediction(location_id, day, hour, ai_congestion)

if __name__ == "__main__":
    run_batch_prediction()
