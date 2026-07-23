"""
구글 광고 — 키워드 레벨 실시간 조회.
기존 sabang_report/app.py 의 _google_client() / get_g_keyword_data() 패턴 재사용.

반환: (records, logs)
 record = {device_raw, date, campaign, adGroup, adName, imp, click, cost, rank, view}
 (cost 는 VAT 미포함 원값 — costMultiplier 는 pipeline 에서 적용)
"""

_DEVICE_MAP = {"DESKTOP": "PC", "MOBILE": "모바일", "TABLET": "모바일"}


def _client(secrets):
    from google.ads.googleads.client import GoogleAdsClient
    cfg = {
        "developer_token": secrets["GADS_DEVELOPER_TOKEN"],
        "client_id": secrets["GADS_CLIENT_ID"],
        "client_secret": secrets["GADS_CLIENT_SECRET"],
        "refresh_token": secrets["GADS_REFRESH_TOKEN"],
        "use_proto_plus": True,
    }
    login_cid = secrets.get("GADS_LOGIN_CUSTOMER_ID")
    if login_cid:
        cfg["login_customer_id"] = str(login_cid).replace("-", "")
    return GoogleAdsClient.load_from_dict(cfg)


def _customer_ids(secrets):
    raw = secrets.get("GADS_CUSTOMER_IDS") or secrets.get("GADS_CUSTOMER_ID") or ""
    ids = [str(x).strip().replace("-", "") for x in str(raw).split(",")]
    return [x for x in ids if x]


def is_configured(secrets):
    need = ["GADS_DEVELOPER_TOKEN", "GADS_CLIENT_ID", "GADS_CLIENT_SECRET", "GADS_REFRESH_TOKEN"]
    return all(secrets.get(k) for k in need) and bool(_customer_ids(secrets))


def get_google_rows(since, until, secrets, logs=None):
    if logs is None:
        logs = []
    if not is_configured(secrets):
        logs.append("⚠️ [구글] 자격증명 없음 — 건너뜀")
        return [], logs

    try:
        client = _client(secrets)
    except Exception as e:
        logs.append(f"❌ [구글] 클라이언트 생성 실패: {e}")
        return [], logs

    ga = client.get_service("GoogleAdsService")
    query = f"""
        SELECT
          segments.date, campaign.name, ad_group.name,
          ad_group_criterion.keyword.text, segments.device,
          metrics.impressions, metrics.clicks, metrics.cost_micros
        FROM keyword_view
        WHERE segments.date BETWEEN '{since}' AND '{until}'
          AND campaign.advertising_channel_type = SEARCH
    """.strip()

    records = []
    for cust_id in _customer_ids(secrets):
        try:
            logs.append(f"[구글] customer_id={cust_id} 조회 시작")
            stream = ga.search_stream(customer_id=cust_id, query=query)
            count = 0
            for batch in stream:
                for r in batch.results:
                    records.append({
                        "device_raw": _DEVICE_MAP.get(r.segments.device.name, "모바일"),
                        "date": str(r.segments.date),
                        "campaign": r.campaign.name,
                        "adGroup": r.ad_group.name,
                        "adName": r.ad_group_criterion.keyword.text,
                        "imp": int(r.metrics.impressions),
                        "click": int(r.metrics.clicks),
                        "cost": round(r.metrics.cost_micros / 1_000_000, 2),
                        "rank": 0,
                        "view": 0,
                    })
                    count += 1
            logs.append(f"[구글] customer_id={cust_id} rows={count}")
        except Exception as e:
            logs.append(f"❌ [구글] 조회 실패 cust_id={cust_id} err={e}")
    return records, logs
