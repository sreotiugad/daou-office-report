"""
공통 헬퍼 — GAS "RAW 생성기 v5"의 3절(공통 헬퍼)을 1:1 포팅.
"""
import re
from datetime import datetime, date

from .config import BRAND_OVERRIDE_RULES


def to_num(v):
    if v is None or v == "" or v == "-":
        return 0
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0
        return 0 if f != f else f  # NaN 체크
    s = str(v).replace(",", "").replace("₩", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0


def to_str(v):
    if v is None:
        return ""
    # pandas NaN
    if isinstance(v, float) and v != v:
        return ""
    return str(v).strip()


def _pad2(n):
    return ("0" + str(n))[-2:]


def normalize_date(v):
    """다양한 날짜 표기를 yyyy-MM-dd 문자열로 통일."""
    if v is None or v == "":
        return ""
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if s in ("nan", "NaT"):
        return ""
    if re.fullmatch(r"\d{8}", s):
        return s[0:4] + "-" + s[4:6] + "-" + s[6:8]
    s = re.sub(r"\.$", "", s)
    m = re.fullmatch(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if m:
        return m.group(1) + "-" + _pad2(int(m.group(2))) + "-" + _pad2(int(m.group(3)))
    # 최후: pandas 파싱 시도
    try:
        import pandas as pd
        d = pd.to_datetime(s, errors="coerce")
        if d is not None and not pd.isna(d):
            return d.strftime("%Y-%m-%d")
    except Exception:
        pass
    return s


def normalize_device(raw):
    s = to_str(raw)
    if not s or s == "-":
        return ""
    low = s.lower()
    if re.search(r"pc|컴퓨터|데스크|desktop", low):
        return "PC"
    if re.search(r"모바일|휴대|폰|mobile|tablet|태블릿", low):
        return "모바일"
    return s


def norm_cmp(v):
    """비교용 정규화 (대소문자·공백 무시)."""
    return re.sub(r"\s+", "", to_str(v).lower())


def norm_medium(v):
    """소스/매체 비교용 정규화."""
    s = to_str(v).lower()
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\s+", " ", s)
    return s


def make_join_key(brand, gubun, media, device, ymd, content):
    """★ GA 결합 키 (v5 핵심): 브랜드|구분|매체|디바이스|날짜|광고이름(콘텐츠)."""
    return "|".join([
        norm_cmp(brand), norm_cmp(gubun), norm_cmp(media),
        norm_cmp(normalize_device(device)),
        to_str(ymd), norm_cmp(content),
    ])


def make_meta_key(campaign, adset, ad_name):
    return to_str(campaign) + "|" + to_str(adset) + "|" + to_str(ad_name)


def make_ga_index_key(brand, medium):
    return to_str(brand).lower() + "|" + norm_medium(medium)


def apply_brand_override(brand, gubun, ad_group):
    """브랜드 보정: 조건에 맞으면 바뀐 브랜드, 아니면 원래 브랜드."""
    g = norm_cmp(gubun)
    ag = to_str(ad_group).lower()
    for rule in BRAND_OVERRIDE_RULES:
        if norm_cmp(rule["whenGubun"]) != g:
            continue
        if str(rule["adGroupContains"]).lower() not in ag:
            continue
        return rule["setBrand"]
    return brand
