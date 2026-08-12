"""
네이버 검색광고 API 소스 — 키워드 단위 일별 성과 조회.

사방넷 앱(app.py)의 검증된 네이버 통계리포트 로직을 다우용으로 포팅.
 · 이름맵(캠페인/광고그룹/키워드 id→name)은 Master Report 3회 호출로 구성
 · 일자별 AD 통계리포트를 생성·폴링·다운로드·파싱
 · 캠페인명으로 브랜드/구분은 매체 INDEX가 매핑(여기선 이름만 채움)

반환: (records, logs)
 record = {device_raw, date, campaign, adGroup, adName, imp, click, cost, rank, view}
   (업로드 파서와 동일 스키마 → 파이프라인 그대로 사용)

secrets 필요 키
   NAVER_CUSTOMER_ID / NAVER_API_KEY / NAVER_SECRET_KEY
"""
import base64
import hashlib
import hmac
import io
import json
import time
import zipfile
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

import pandas as pd
import requests

from ..helpers import to_num, to_str, normalize_date, normalize_device

NAVER_BASE_URL = "https://api.searchad.naver.com"


def is_configured(secrets):
    return bool(secrets.get("NAVER_CUSTOMER_ID") and secrets.get("NAVER_API_KEY")
               and secrets.get("NAVER_SECRET_KEY"))


def _acc(secrets):
    return {
        "customer_id": to_str(secrets.get("NAVER_CUSTOMER_ID")),
        "api_key": to_str(secrets.get("NAVER_API_KEY")),
        "secret_key": to_str(secrets.get("NAVER_SECRET_KEY")),
    }


def _headers(acc, uri, method="GET"):
    ts = str(int(time.time() * 1000))
    msg = f"{ts}.{method.upper().strip()}.{str(uri).strip()}"
    secret = str(acc["secret_key"]).strip().encode("utf-8")
    sig = base64.b64encode(hmac.new(secret, msg.encode("utf-8"), hashlib.sha256).digest()).decode()
    return {
        "X-Timestamp": ts,
        "X-API-KEY": str(acc["api_key"]).strip(),
        "X-Customer": str(acc["customer_id"]).strip(),
        "X-Signature": sig,
        "Content-Type": "application/json",
    }


def _download(acc, download_url):
    full = download_url if str(download_url).startswith("http") else (NAVER_BASE_URL + download_url)
    u = urlparse(full)
    path = u.path
    params = {k: v[0] for k, v in parse_qs(u.query).items() if v}
    r = requests.get(NAVER_BASE_URL + path, params=params, headers=_headers(acc, path, "GET"), timeout=120)
    if r.status_code != 200:
        raise Exception(f"report-download 실패 status={r.status_code} body={r.text[:200]}")
    return r.content


def _unzip_if_needed(content):
    if content[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(content))
        return z.read(z.namelist()[0])
    return content


# ── 이름맵: Master Report ────────────────────────────────────
_MASTER_COLS = {
    "Campaign": {"id_col": 1, "name_col": 2, "id_key": "id", "name_key": "name"},
    "Adgroup":  {"id_col": 1, "name_col": 3, "id_key": "id", "name_key": "name"},
    "Keyword":  {"id_col": 2, "name_col": 3, "id_key": "id", "name_key": "name"},
}


def _master_report(acc, item, logs):
    uri = "/master-reports"
    try:
        r = requests.post(NAVER_BASE_URL + uri, headers=_headers(acc, uri, "POST"),
                          json={"item": item}, timeout=30)
    except Exception as e:
        logs.append(f"❌ [네이버] MasterReport {item} 요청 오류: {e}")
        return {}
    if r.status_code not in (200, 201):
        logs.append(f"❌ [네이버] MasterReport {item} 생성 실패 status={r.status_code}")
        return {}
    job_id = (r.json() or {}).get("id")
    if not job_id:
        return {}

    download_url = None
    for _ in range(30):
        su = f"/master-reports/{job_id}"
        try:
            rs = requests.get(NAVER_BASE_URL + su, headers=_headers(acc, su, "GET"), timeout=30)
        except Exception:
            time.sleep(2)
            continue
        if rs.status_code == 200:
            st = rs.json()
            status = str(st.get("status", "")).upper()
            du = st.get("downloadUrl") or st.get("downloadURL")
            if status == "BUILT" and du:
                download_url = du
                break
            if status in ("ERROR", "NONE"):
                logs.append(f"❌ [네이버] MasterReport {item} 빌드 실패 {status}")
                return {}
        time.sleep(2)
    if not download_url:
        logs.append(f"⚠️ [네이버] MasterReport {item} downloadUrl 없음(timeout)")
        return {}

    try:
        content = _unzip_if_needed(_download(acc, download_url))
    except Exception as e:
        logs.append(f"❌ [네이버] MasterReport {item} 다운로드 실패: {e}")
        return {}

    # JSON 우선, 실패 시 TSV 폴백
    id2name = {}
    try:
        data = json.loads(content.decode("utf-8"))
        rows = data if isinstance(data, list) else data.get("items", data.get("data", []))
        for row in rows:
            cid = row.get("nccCampaignId") or row.get("nccAdgroupId") or row.get("nccKeywordId") or row.get("id")
            name = row.get("name") or row.get("keyword") or row.get("campaignName") or row.get("adgroupName")
            if cid and name:
                id2name[cid] = name
        if id2name:
            logs.append(f"[네이버] MasterReport {item}: {len(id2name)}개")
            return id2name
    except Exception:
        pass

    try:
        cfg = _MASTER_COLS[item]
        text = content.decode("utf-8")
        for line in text.splitlines():
            if not line.strip():
                continue
            cols = line.split("\t")
            try:
                id2name[cols[cfg["id_col"]].strip()] = cols[cfg["name_col"]].strip()
            except IndexError:
                continue
        logs.append(f"[네이버] MasterReport {item}(TSV): {len(id2name)}개")
    except Exception as e:
        logs.append(f"❌ [네이버] MasterReport {item} 파싱 실패: {e}")
    return id2name


def _build_name_maps(acc, logs):
    camp = _master_report(acc, "Campaign", logs)
    grp = _master_report(acc, "Adgroup", logs)
    kw = _master_report(acc, "Keyword", logs)
    return camp, grp, kw


# ── 일자별 AD 통계리포트 ─────────────────────────────────────
def _create_stat_report(acc, day, report_tp="AD", stat_level="KEYWORD"):
    uri = "/stat-reports"
    payload = {"reportTp": report_tp, "statDt": day, "statLevel": stat_level}
    r = requests.post(NAVER_BASE_URL + uri, headers=_headers(acc, uri, "POST"), json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise Exception(f"/stat-reports {r.status_code} {r.text[:200]}")
    return r.json()


def _fetch_day(acc, day, camp_map, grp_map, kw_map, logs):
    try:
        job = _create_stat_report(acc, day)
    except Exception as e:
        logs.append(f"❌ [네이버] stat-report 생성 실패 day={day}: {e}")
        return None
    job_id = job.get("reportJobId") or job.get("reportJobID") or job.get("reportId")
    if not job_id:
        logs.append(f"❌ [네이버] job_id 없음 day={day}")
        return None

    download_url = None
    for _ in range(30):
        su = f"/stat-reports/{job_id}"
        try:
            rs = requests.get(NAVER_BASE_URL + su, headers=_headers(acc, su, "GET"), timeout=30)
        except Exception:
            time.sleep(2)
            continue
        st = rs.json() if rs.status_code == 200 else {}
        status = str(st.get("status", "")).upper()
        du = st.get("downloadUrl") or st.get("downloadURL") or st.get("download_url")
        if status in ("BUILT", "DONE", "COMPLETED", "SUCCESS") and du:
            download_url = du
            break
        if status in ("ERROR", "FAIL", "FAILED"):
            logs.append(f"❌ [네이버] 리포트 빌드 실패 day={day} status={status}")
            return None
        time.sleep(2)
    if not download_url:
        logs.append(f"⚠️ [네이버] downloadUrl 없음 day={day}(timeout)")
        return None

    try:
        content = _unzip_if_needed(_download(acc, download_url))
        txt = content.decode("utf-8", errors="replace")
    except Exception as e:
        logs.append(f"❌ [네이버] 다운로드/디코드 실패 day={day}: {e}")
        return None

    lines = [ln for ln in txt.splitlines() if ln.strip()]
    if not lines:
        return None
    col_count = len(lines[0].split("\t"))
    # AD 리포트: clkAmt=광고비, convAmt=노출순위 가중합
    base_cols = ["statDt", "customerId", "campaignId", "adgroupId", "keywordId",
                 "adId", "bsnId", "bidAmt", "pcMblTp",
                 "impCnt", "clkCnt", "clkAmt", "convAmt", "avgRnk"]
    if col_count > len(base_cols):
        base_cols = base_cols + [f"extra{i}" for i in range(col_count - len(base_cols))]
    elif col_count < len(base_cols):
        base_cols = base_cols[:col_count]
    try:
        df = pd.read_csv(io.StringIO("\n".join(lines)), sep="\t", header=None,
                         names=base_cols, engine="python")
    except Exception as e:
        logs.append(f"❌ [네이버] 파싱 실패 day={day} cols={col_count}: {e}")
        return None

    df["campaignName"] = df["campaignId"].map(camp_map).fillna(df["campaignId"])
    df["adgroupName"] = df["adgroupId"].map(grp_map).fillna(df["adgroupId"])
    df["keywordName"] = df["keywordId"].map(kw_map).fillna(df["keywordId"])
    # BS(브랜드검색) 캠페인은 클릭과금이 아니므로 그대로 두되 클릭비용만 사용
    logs.append(f"[네이버] day={day} rows={len(df)} cols={col_count}")
    return df


def _dev(pcmbl):
    s = str(pcmbl or "").upper().strip()
    if s in ("P", "PC"):
        return "PC"
    if s in ("M", "MOBILE", "MO"):
        return "모바일"
    return normalize_device(pcmbl)


def get_naver_api_rows(secrets, since, until, logs=None):
    """네이버 검색광고 API로 키워드 단위 일별 성과 → records."""
    if logs is None:
        logs = []
    if not is_configured(secrets):
        logs.append("⚠️ [네이버] API 자격증명 없음 — 업로드 파일 사용")
        return [], logs

    acc = _acc(secrets)
    try:
        camp_map, grp_map, kw_map = _build_name_maps(acc, logs)
    except Exception as e:
        logs.append(f"❌ [네이버] 이름맵 구성 실패: {e}")
        camp_map = grp_map = kw_map = {}

    # 날짜 목록 (yyyymmdd)
    d = datetime.strptime(since, "%Y-%m-%d").date()
    e = datetime.strptime(until, "%Y-%m-%d").date()
    days = []
    while d <= e:
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    frames = []
    for day in days:
        df = _fetch_day(acc, day, camp_map, grp_map, kw_map, logs)
        if df is not None and len(df):
            frames.append(df)
    if not frames:
        logs.append("⚠️ [네이버] API 결과 없음")
        return [], logs

    alldf = pd.concat(frames, ignore_index=True)
    for c in ["impCnt", "clkCnt", "clkAmt", "convAmt"]:
        if c in alldf.columns:
            alldf[c] = pd.to_numeric(alldf[c], errors="coerce").fillna(0)

    # 키워드×기기×일자 집계
    keys = [c for c in ["statDt", "campaignName", "adgroupName", "keywordName", "pcMblTp"] if c in alldf.columns]
    agg = {c: "sum" for c in ["impCnt", "clkCnt", "clkAmt", "convAmt"] if c in alldf.columns}
    g = alldf.groupby(keys, as_index=False).agg(agg)

    records = []
    for _, r in g.iterrows():
        imp = to_num(r.get("impCnt"))
        # 평균노출순위 = 노출순위 가중합(convAmt) / 노출수
        rank = round(to_num(r.get("convAmt")) / imp, 1) if imp else 0
        records.append({
            "device_raw": _dev(r.get("pcMblTp")),
            "date": normalize_date(str(r.get("statDt"))),
            "campaign": to_str(r.get("campaignName")),
            "adGroup": to_str(r.get("adgroupName")),
            "adName": to_str(r.get("keywordName")),
            "imp": imp,
            "click": to_num(r.get("clkCnt")),
            "cost": to_num(r.get("clkAmt")),
            "rank": rank,
            "view": 0,
        })
    logs.append(f"✅ [네이버] API 완료: {len(records)}행")
    return records, logs
