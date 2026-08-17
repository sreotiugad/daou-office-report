"""
매핑표 저장소 — 매체 INDEX / GA INDEX / meta 정리 3종을 data/*.csv로 영속.

GAS 원본은 구글시트의 INDEX 시트(A·B·C열, G~K열)와 [meta 정리] 시트를 읽었으나,
여기서는 Streamlit 안에서 편집·업로드하고 CSV로 저장한다.

각 표를 파이프라인이 쓰는 룩업 dict 로 변환하는 build_* 함수도 함께 제공한다.
"""
import os
import pandas as pd

from .config import UNCLASSIFIED
from .helpers import to_str, norm_medium, normalize_device, make_ga_index_key, make_meta_key

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# 각 매핑표의 파일명과 컬럼 스키마
MEDIA_INDEX_COLS = ["캠페인", "브랜드", "구분"]
GA_INDEX_COLS = ["브랜드", "소스/매체", "구분", "매체", "디바이스"]
META_MAP_COLS = ["캠페인", "광고세트", "광고이름", "ga캠페인", "ga컨텐츠"]
# 네이버 브랜드검색(고정 계약) — 기간 내 매일 '일일광고비'를 지정 행에 채운다.
BRAND_SEARCH_COLS = ["브랜드", "구분", "매체", "디바이스", "캠페인", "광고그룹",
                     "광고이름", "시작일", "종료일", "일일광고비"]

_FILES = {
    "media_index": ("media_index.csv", MEDIA_INDEX_COLS),
    "ga_index": ("ga_index.csv", GA_INDEX_COLS),
    "meta_map": ("meta_map.csv", META_MAP_COLS),
    "brand_search": ("brand_search.csv", BRAND_SEARCH_COLS),
}


def _path(key):
    return os.path.join(_DATA_DIR, _FILES[key][0])


def ensure_data_dir():
    os.makedirs(_DATA_DIR, exist_ok=True)


def _get_secrets():
    """Streamlit secrets 를 안전하게 dict 로. (streamlit 밖이면 빈 dict)"""
    try:
        import streamlit as st
        return dict(st.secrets)
    except Exception:
        return {}


def _local_load(key):
    """로컬 CSV(= repo 배포 기준값)를 DataFrame 으로. 없으면 빈 스키마."""
    cols = _FILES[key][1]
    p = _path(key)
    if os.path.exists(p):
        try:
            df = pd.read_csv(p, dtype=str).fillna("")
            for c in cols:
                if c not in df.columns:
                    df[c] = ""
            return df[cols]
        except Exception:
            pass
    return pd.DataFrame(columns=cols)


def storage_kind():
    """현재 저장소 종류: 'sheet' 또는 'local'."""
    from . import sheets_store
    return "sheet" if sheets_store.is_configured(_get_secrets()) else "local"


def load_table(key):
    """매핑표 로드. 시트가 설정돼 있으면 시트, 아니면 로컬 CSV.
    시트 사용 시 로컬 CSV 는 워크시트가 없을 때의 초기 시드 값으로 쓰인다."""
    cols = _FILES[key][1]
    secrets = _get_secrets()
    from . import sheets_store
    local = _local_load(key)
    if sheets_store.is_configured(secrets):
        try:
            return sheets_store.read_df(key, cols, secrets, seed_df=local)
        except Exception:
            # 시트 접근 실패 시 로컬 값으로 폴백(앱이 죽지 않게)
            return local
    return local


def save_table(key, df):
    """매핑표 저장. 시트가 설정돼 있으면 시트, 아니면 로컬 CSV."""
    cols = _FILES[key][1]
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = ""
    out = out[cols].fillna("").astype(str)

    secrets = _get_secrets()
    from . import sheets_store
    if sheets_store.is_configured(secrets):
        sheets_store.write_df(key, out, cols, secrets)
        return

    ensure_data_dir()
    out.to_csv(_path(key), index=False, encoding="utf-8-sig")


# ── 파이프라인용 룩업 변환 ──────────────────────────────────

def build_media_index_map(df):
    """{캠페인: {'brand':..., 'gubun':...}}."""
    m = {}
    for _, row in df.iterrows():
        campaign = to_str(row.get("캠페인"))
        if not campaign:
            continue
        m[campaign] = {
            "brand": to_str(row.get("브랜드")) or UNCLASSIFIED,
            "gubun": to_str(row.get("구분")) or UNCLASSIFIED,
        }
    return m


def build_ga_index_map(df):
    """GAS buildGaIndexMap 상당.
    반환: {byKey, byMedium, whitelist, count}
    """
    result = {"byKey": {}, "byMedium": {}, "whitelist": {}, "count": 0}
    for _, row in df.iterrows():
        brand = to_str(row.get("브랜드"))
        medium_raw = to_str(row.get("소스/매체"))
        if not medium_raw:
            continue
        info = {
            "gubun": to_str(row.get("구분")) or UNCLASSIFIED,
            "media": to_str(row.get("매체")) or UNCLASSIFIED,
            "device": normalize_device(row.get("디바이스")),
            "deviceRaw": to_str(row.get("디바이스")),
        }
        nm = norm_medium(medium_raw)
        result["whitelist"][nm] = True
        if nm not in result["byMedium"]:
            result["byMedium"][nm] = info
        if brand:
            result["byKey"][make_ga_index_key(brand, medium_raw)] = info
        result["count"] += 1
    return result


def build_meta_content_map(df):
    """GAS buildMetaContentMap 상당. 반환: {data:{key:content}, count}."""
    m = {"data": {}, "count": 0}
    for _, row in df.iterrows():
        content = to_str(row.get("ga컨텐츠"))
        if not content:
            continue
        key = make_meta_key(row.get("캠페인"), row.get("광고세트"), row.get("광고이름"))
        m["data"][key] = content
        m["count"] += 1
    return m


def build_brand_search_contracts(df):
    """브랜드검색 계약표 → 계약 dict 리스트. 일일광고비가 있는 행만."""
    from .helpers import to_num, normalize_date
    out = []
    for _, row in df.iterrows():
        fee = to_num(row.get("일일광고비"))
        adname = to_str(row.get("광고이름"))
        if fee <= 0 or not adname:
            continue
        out.append({
            "brand": to_str(row.get("브랜드")) or UNCLASSIFIED,
            "gubun": to_str(row.get("구분")) or UNCLASSIFIED,
            "media": to_str(row.get("매체")) or "네이버",
            "device": normalize_device(row.get("디바이스")),
            "campaign": to_str(row.get("캠페인")),
            "adGroup": to_str(row.get("광고그룹")),
            "adName": adname,
            "since": normalize_date(row.get("시작일")),
            "until": normalize_date(row.get("종료일")),
            "fee": fee,
        })
    return out
