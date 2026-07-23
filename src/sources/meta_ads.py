"""
메타(페이스북/인스타그램) 광고 — 광고(ad) 레벨 실시간 조회.
기존 sabang_report/app.py 의 get_meta_data() 패턴 재사용.

GAS 설정상 메타는 디바이스 고정("모바일")이고, 결합 콘텐츠는 [meta 정리]에서
(캠페인+광고세트+광고이름) → ga컨텐츠 로 매핑되므로, 여기서는 ad 레벨로 조회해
campaign_name / adset_name(광고그룹) / ad_name(광고이름) 을 그대로 넘긴다.

반환: (records, logs)
 record = {device_raw, date, campaign, adGroup, adName, imp, click, cost, rank, view}
"""
import json
import requests

_GRAPH_BASE = "https://graph.facebook.com/v21.0"


def is_configured(secrets):
    return bool(secrets.get("META_ACCESS_TOKEN")) and bool(secrets.get("META_AD_ACCOUNT_ID"))


def get_meta_rows(since, until, secrets, logs=None):
    if logs is None:
        logs = []
    if not is_configured(secrets):
        logs.append("⚠️ [메타] 자격증명 없음 — 건너뜀")
        return [], logs

    token = secrets["META_ACCESS_TOKEN"]
    acct = str(secrets["META_AD_ACCOUNT_ID"])
    account = acct if acct.startswith("act_") else f"act_{acct}"
    url = f"{_GRAPH_BASE}/{account}/insights"
    params = {
        "access_token": token,
        "fields": "campaign_name,adset_name,ad_name,impressions,clicks,spend,actions",
        "time_range": json.dumps({"since": since, "until": until}),
        "time_increment": "1",
        "level": "ad",
        "limit": "500",
    }

    records = []
    try:
        while True:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code != 200:
                logs.append(f"❌ [메타] API 실패 status={r.status_code} body={r.text[:300]}")
                break
            j = r.json()
            for item in j.get("data", []):
                views = 0
                for a in (item.get("actions") or []):
                    if a.get("action_type") in ("video_view", "video_thruplay_watched_actions"):
                        views += float(a.get("value", 0) or 0)
                records.append({
                    "device_raw": "모바일",
                    "date": str(item.get("date_start", ""))[:10],
                    "campaign": str(item.get("campaign_name", "")),
                    "adGroup": str(item.get("adset_name", "")),
                    "adName": str(item.get("ad_name", "")),
                    "imp": int(item.get("impressions", 0) or 0),
                    "click": int(item.get("clicks", 0) or 0),
                    "cost": float(item.get("spend", 0) or 0),
                    "rank": 0,
                    "view": views,
                })
            next_url = j.get("paging", {}).get("next")
            if not next_url:
                break
            url = next_url
            params = {}
        logs.append(f"[메타] 완료 rows={len(records)}")
    except Exception as e:
        logs.append(f"❌ [메타] 오류: {e}")
    return records, logs
