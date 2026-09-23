# telemetry.py
import requests

WEBHOOK_URL = "https://ais-pre-644fqsjp5hnrfiiwh2afju-301922375646.europe-west2.run.app/api/webhook/activity"

def log_to_dashboard(user_id, full_name, username="", schedule_type="ideal"):
    """
    schedule_type:
      - 'ideal'      -> جدول مواد
      - 'simplified' -> جدول اوقات
      - 'full'       -> جدول كل المواد
    """
    titles = {
        'ideal': 'إنشاء جدول مواد',
        'simplified': 'إنشاء جدول اوقات',
        'full': 'استعراض جدول كل المواد'
    }
    
    payload = {
        "telegramId": str(user_id),
        "userName": full_name or "طالب",
        "username": username or "",
        "action": f"generate_{schedule_type}_schedule",
        "actionTitle": titles.get(schedule_type, "طلب جدول"),
        "scheduleType": schedule_type
    }
    
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=2)
    except Exception:
        pass  # لضمان عدم توقف البوت في حال حدوث انقطاع إنترنت
