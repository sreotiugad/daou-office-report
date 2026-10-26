"""
ChatGPT(OpenAI) 광고 — 광고(ad) 레벨 일별 실시간 조회.
OpenAI Advertiser API: GET /v1/ad_account/insights (aggregation_level=ad, daily)
문서: https://developers.openai.com/ads/api-reference/insights

키는 광고계정 단위(ads.openai.com → 설정 → API 키)라 계정 ID는 따로 필요 없다.
날짜는 Asia/Seoul 기준으로 받아 GA 날짜와 맞춘다.

GA 결합: 광고 링크(ad.link)의 utm_content 를 결합값(joinContent)으로 넘긴다.
 (광고이름 branding_261022 ↔ utm_content img_branding_261022_261022 처럼
  이름과 UTM 값이 달라서, 링크에서 직접 뽑아야 정확히 붙는다)

반환: (records, logs)
 record = {device_raw, date, campaign, adGroup, adName, imp, click, cost, rank, view, joinContent}
"""
import json
from urllib.parse import urlparse, parse_qs

import requests

_API_BASE = "https://api.ads.openai.com/v1"
_TIMEZONE = "Asia/Seoul"
_FIELDS = [
    "metadata.readable_time",
    "campaign.name",
    "ad_group.name",
    "ad.name",
    "ad.link",
    "ad.impressions",
    "ad.clicks",
    "ad.spend",
]


def is_configured(secrets):
    return bool(secrets.get("OPENAI_ADS_API_KEY"))


def _pick(item, *keys):
    """응답 키가 flat(ad_name) 또는 canonical(ad.name) 어느 쪽이어도 읽는다."""
    for k in keys:
        if item.get(k) not in (None, ""):
            return item[k]
    return None


def _utm_content(url):
    if not url:
        return ""
    try:
        vals = parse_qs(urlparse(str(url)).query).get("utm_content")
        return vals[0].strip() if vals else ""
    except Exception:
        return ""


def get_openai_rows(since, until, secrets, logs=None):
    if logs is None:
        logs = []
    if not is_configured(secrets):
        logs.append("⚠️ [ChatGPT] 자격증명 없음 — 건너뜀")
        return [], logs

    headers = {"Authorization": f"Bearer {secrets['OPENAI_ADS_API_KEY']}"}
    url = f"{_API_BASE}/ad_account/insights"
    base_params = [
        ("aggregation_level", "ad"),
        ("time_granularity", "daily"),
        ("time_ranges[]", json.dumps({"type": "date_range", "since": since,
                                      "until": until, "timezone": _TIMEZONE})),
        ("limit", "2000"),
    ] + [("fields[]", f) for f in _FIELDS]

    records = []
    no_utm = 0
    after = None
    try:
        while True:
            params = base_params + ([("after", after)] if after else [])
            r = requests.get(url, headers=headers, params=params, timeout=60)
            if r.status_code != 200:
                logs.append(f"❌ [ChatGPT] API 실패 status={r.status_code} body={r.text[:300]}")
                break
            j = r.json()
            for item in j.get("data", []):
                link = _pick(item, "ad_link", "ad.link", "link")
                content = _utm_content(link)
                if not content:
                    no_utm += 1
                records.append({
                    "device_raw": "",  # config 고정값 사용
                    "date": str(_pick(item, "readable_time", "metadata.readable_time") or "")[:10],
                    "campaign": str(_pick(item, "campaign_name", "campaign.name") or ""),
                    "adGroup": str(_pick(item, "ad_group_name", "ad_group.name") or ""),
                    "adName": str(_pick(item, "ad_name", "ad.name") or ""),
                    "imp": int(float(_pick(item, "impressions", "ad_impressions", "ad.impressions") or 0)),
                    "click": int(float(_pick(item, "clicks", "ad_clicks", "ad.clicks") or 0)),
                    "cost": float(_pick(item, "spend", "ad_spend", "ad.spend") or 0),
                    "rank": 0,
                    "view": 0,
                    "joinContent": content,
                })
            if not j.get("has_more") or not j.get("last_id"):
                break
            after = j["last_id"]
        logs.append(f"[ChatGPT] 완료 rows={len(records)}")
        if no_utm:
            logs.append(f"⚠️ [ChatGPT] 링크에 utm_content 없는 행 {no_utm}개 — 광고이름으로 GA 결합 시도")
    except Exception as e:
        logs.append(f"❌ [ChatGPT] 오류: {e}")
    return records, logs
