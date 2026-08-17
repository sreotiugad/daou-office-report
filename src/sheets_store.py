"""
구글시트 저장소 — 매핑표(매체 INDEX / GA INDEX / meta 정리 / 고정비)를
구글 스프레드시트의 워크시트(탭)에 영구 저장한다.

Streamlit Community Cloud 등은 파일시스템이 임시(ephemeral)라 data/*.csv 에
저장해도 재배포 시 사라진다. 그래서 시트를 저장소로 쓰면
 · 앱에서 편집·저장한 매핑이 영구 보존되고
 · 시트에서 직접 편집해도 앱에 반영된다.

인증은 GA4 와 동일한 서비스 계정(GA4_SERVICE_ACCOUNT_JSON)을 재활용한다.
다만 스코프가 다르므로(스프레드시트 쓰기) 별도 자격증명을 만든다.

secrets 필요 키
   GA4_SERVICE_ACCOUNT_JSON : 서비스 계정 JSON (문자열 또는 dict) — GA4 와 공용
   MAPPING_SHEET_ID         : 매핑표를 저장할 스프레드시트 ID

준비(1회):
   1) 구글 클라우드 콘솔에서 해당 서비스계정 프로젝트에 'Google Sheets API' 사용 설정
   2) 스프레드시트 1개 생성 → 서비스계정 이메일(client_email)을 '편집자'로 공유
   3) 그 시트의 ID(주소 /d/<ID>/edit)를 MAPPING_SHEET_ID 로 넣기
   워크시트(탭)는 없으면 앱이 자동 생성하고, repo 의 data/*.csv 값으로 자동 시드한다.
"""
import json

import pandas as pd

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _sa_info(secrets):
    raw = secrets.get("GA4_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def is_configured(secrets):
    """서비스계정 JSON 과 시트 ID 가 모두 있어야 시트 저장소를 쓴다."""
    return bool(secrets.get("GA4_SERVICE_ACCOUNT_JSON") and secrets.get("MAPPING_SHEET_ID"))


def _creds(secrets):
    from google.oauth2 import service_account
    info = _sa_info(secrets)
    return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)


def _open(secrets):
    import gspread
    gc = gspread.authorize(_creds(secrets))
    return gc.open_by_key(str(secrets["MAPPING_SHEET_ID"]).strip())


def _align(df, cols):
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols].fillna("")


def _fetch_values(sheet_id, title):
    """워크시트 전체 값(list[list]). 워크시트가 없으면 None.
    캐시는 mapping_store 쪽에서 감싼다(여기선 순수 IO)."""
    import gspread
    import streamlit as st
    secrets = dict(st.secrets)
    gc = gspread.authorize(_creds(secrets))
    sh = gc.open_by_key(str(sheet_id).strip())
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return None
    return ws.get_all_values()


# Streamlit 캐시로 감싼 버전(있으면 사용). 매핑 탭은 매 rerun마다 4개 표를
# 읽으므로 캐시가 없으면 느리다. 저장 시 write_df 가 캐시를 비운다.
try:
    import streamlit as _st

    _fetch_values_cached = _st.cache_data(ttl=600, show_spinner=False)(_fetch_values)
except Exception:  # streamlit 밖(테스트 등)에서는 캐시 없이
    _fetch_values_cached = _fetch_values


def diagnose(secrets):
    """시트 연결 상태를 실제로 확인. (ok: bool, message: str)"""
    if not secrets.get("GA4_SERVICE_ACCOUNT_JSON"):
        return False, "서비스계정 JSON(GA4_SERVICE_ACCOUNT_JSON) 이 secrets 에 없습니다."
    if not secrets.get("MAPPING_SHEET_ID"):
        return False, "MAPPING_SHEET_ID 가 secrets 에 없습니다 → 로컬 CSV 로 동작 중."
    try:
        info = _sa_info(secrets)
        email = (info or {}).get("client_email", "(알수없음)")
    except Exception as e:
        return False, f"서비스계정 JSON 파싱 실패: {e}"
    try:
        sh = _open(secrets)
        titles = [ws.title for ws in sh.worksheets()]
        return True, (f"연결 OK · 시트명='{sh.title}' · 워크시트={titles or '(없음)'}\n"
                      f"서비스계정: {email}")
    except Exception as e:
        return False, (f"{type(e).__name__}: {e}\n"
                       f"→ 이 시트를 서비스계정 이메일에 '편집자'로 공유했는지 확인하세요.\n"
                       f"   공유할 이메일: {email}\n"
                       f"→ Google Sheets API 가 켜져 있는지도 확인하세요.")


def read_df(key, cols, secrets, seed_df=None):
    """시트에서 표를 읽어 DataFrame 으로. 워크시트가 없으면 생성 후 seed_df 로 시드."""
    sheet_id = str(secrets["MAPPING_SHEET_ID"]).strip()
    vals = _fetch_values_cached(sheet_id, key)
    if vals is None:  # 워크시트 없음 → 생성 + 시드(최초 이관)
        seed = seed_df if (seed_df is not None and not seed_df.empty) else pd.DataFrame(columns=cols)
        write_df(key, seed, cols, secrets)
        return _align(seed.copy(), cols)
    if not vals:
        return pd.DataFrame(columns=cols)
    header, rows = vals[0], vals[1:]
    df = pd.DataFrame(rows, columns=header) if rows else pd.DataFrame(columns=header)
    return _align(df, cols)


def write_df(key, df, cols, secrets):
    """DataFrame 을 워크시트에 통째로 덮어쓴다. 없으면 워크시트 생성."""
    import gspread
    sh = _open(secrets)
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = ""
    out = out[cols].fillna("").astype(str)
    values = [list(cols)] + out.values.tolist()
    try:
        ws = sh.worksheet(key)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=key, rows=max(len(out) + 10, 20), cols=max(len(cols) + 2, 10))
    ws.clear()
    ws.update(range_name="A1", values=values)
    # 캐시 무효화(다음 read 는 새 값)
    try:
        _fetch_values_cached.clear()
    except Exception:
        pass
