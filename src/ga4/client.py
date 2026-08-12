"""
GA4 Data API 래퍼 — GAS 의 GA_DO / GA_HR 시트를 대체한다.

서비스 계정 JSON + 속성 ID 로 runReport 를 호출해, 파이프라인이 쓰는
GA 레코드 형태로 반환한다.

 record = {date, medium, campaign, content, conv, emp}
   date    : yyyy-MM-dd
   medium  : 세션 소스/매체 (화이트리스트/INDEX 조회에 사용)
   campaign: 세션 캠페인명
   content : 세션 콘텐츠(광고이름 결합키)
   conv    : 전환수 (GA4_SETTINGS['conversion_event'] 키 이벤트)
   emp     : 직원수 (GA4_SETTINGS['employee_metric'], HR 전용; 없으면 0)

secrets 필요 키
   GA4_SERVICE_ACCOUNT_JSON : 서비스 계정 JSON (문자열 또는 dict)
   GA4_PROPERTY_DO / GA4_PROPERTY_HR : 각 GA4 속성 ID (숫자)
"""
import json

from ..config import GA4_SETTINGS
from ..helpers import normalize_date, to_str, to_num


def _load_credentials(secrets):
    from google.oauth2 import service_account
    raw = secrets.get("GA4_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    if isinstance(raw, str):
        info = json.loads(raw)
    else:
        info = dict(raw)
    return service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/analytics.readonly"]
    )


def is_configured(secrets):
    if not secrets.get("GA4_SERVICE_ACCOUNT_JSON"):
        return False
    return any(secrets.get(s["property_secret"]) for s in _sources_from_config())


def _sources_from_config():
    from ..config import GA_SOURCES
    return GA_SOURCES


def get_ga_records(source, secrets, since, until, logs=None):
    """단일 GA 소스(brand/property)에 대해 GA4 조회 → records."""
    if logs is None:
        logs = []
    prop = secrets.get(source["property_secret"])
    if not prop:
        logs.append(f"⚠️ [{source['sheet']}] 속성 ID({source['property_secret']}) 없음 — 건너뜀")
        return [], logs

    creds = _load_credentials(secrets)
    if creds is None:
        logs.append("⚠️ [GA4] 서비스 계정 JSON 없음 — 건너뜀")
        return [], logs

    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange, Dimension, Metric, RunReportRequest, Filter, FilterExpression,
        )
    except Exception as e:
        logs.append(f"❌ [GA4] 라이브러리 로드 실패: {e}")
        return [], logs

    s = GA4_SETTINGS
    dims = [
        Dimension(name=s["date_dimension"]),
        Dimension(name=s["medium_dimension"]),
        Dimension(name=s["campaign_dimension"]),
        Dimension(name=s["content_dimension"]),
        Dimension(name="eventName"),
    ]
    conv_metric = s.get("conversion_metric", "keyEvents")
    metrics = [Metric(name=conv_metric)]
    # 직원수는 사용자 범위 맞춤 '측정기준'(customUser:employee_size). HR만.
    emp_dim = s.get("employee_dimension") if source.get("has_emp") else None
    if emp_dim:
        dims.append(Dimension(name=emp_dim))
    emp_idx = len(dims) - 1  # 직원수 측정기준의 위치

    # 속성마다 전환(가입) 이벤트 이름이 다름: 소스 설정 우선
    conv_event = source.get("conversion_event") or s["conversion_event"]

    req = RunReportRequest(
        property=f"properties/{str(prop).strip()}",
        date_ranges=[DateRange(start_date=since, end_date=until)],
        dimensions=dims,
        metrics=metrics,
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="eventName",
                string_filter=Filter.StringFilter(value=conv_event),
            )
        ),
        limit=100000,
    )

    try:
        client = BetaAnalyticsDataClient(credentials=creds)
        resp = client.run_report(req)
    except Exception as e:
        logs.append(f"❌ [{source['sheet']}] GA4 조회 실패: {e}")
        return [], logs

    records = []
    for row in resp.rows:
        dv = [d.value for d in row.dimension_values]
        mv = [m.value for m in row.metric_values]
        conv = to_num(mv[0]) if len(mv) > 0 else 0
        emp = to_num(dv[emp_idx]) if (emp_dim and len(dv) > emp_idx) else 0
        records.append({
            "date": normalize_date(dv[0]),
            "medium": to_str(dv[1]),
            "campaign": to_str(dv[2]),
            "content": to_str(dv[3]),
            "conv": conv,
            "emp": emp,
        })
    logs.append(f"[{source['sheet']}] GA4 완료 rows={len(records)}")
    return records, logs
