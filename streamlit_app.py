"""
다우오피스 광고 리포트 — RAW 생성기 (Streamlit)

GAS "RAW 생성기 v5" 로직을 웹앱으로 포팅.
 · 구글·메타 : API 실시간 조회
 · 네이버·사람인 : 엑셀 업로드 후 파싱
 · GA 전환수/직원수 : GA4 Data API
 · 매체 INDEX / GA INDEX / meta 정리 : 앱 안에서 편집·저장
"""
import io
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from src.config import RAW_HEADERS, AD_SOURCES, GA_SOURCES
from src import mapping_store as ms
from src.sources.google_ads import get_google_rows, is_configured as google_ok
from src.sources.meta_ads import get_meta_rows, is_configured as meta_ok
from src.sources.upload import parse_upload
from src.sources.ga_upload import parse_ga_upload
from src.sources.naver_api import get_naver_api_rows, is_configured as naver_ok
from src.ga4.client import get_ga_records, is_configured as ga4_ok
from src.pipeline import run_pipeline
from src.validate import build_check_report

st.set_page_config(page_title="다우오피스 광고 리포트", page_icon="💼", layout="wide")


# ── 블루 테마 (Pretendard · hero · 카드 · 그라데이션) ──────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@400;600;700;800&display=swap');

:root, body, html {
    font-family: 'Pretendard', system-ui, -apple-system, sans-serif !important;
}
[data-testid="stAppViewContainer"] {
    background: linear-gradient(180deg, #f4f8ff 0%, #e9f1ff 100%) !important;
}

.hero {
    border-radius: 28px;
    padding: 38px 46px;
    margin-bottom: 30px;
    background: linear-gradient(135deg, #cfe0ff, #e3edff);
    box-shadow: 0 25px 50px rgba(120,160,240,0.25), 0 10px 20px rgba(120,160,240,0.15);
}
.hero h1 {
    margin: 0; font-weight: 800; font-size: 32px;
    letter-spacing: -0.5px; color: #1d4ed8;
}
.hero p { margin-top: 10px; font-size: 15px; color: #3b6bc4; }

/* 카드 컨테이너 (st.container(border=True)) */
[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 22px !important;
    border: 1px solid rgba(147,180,240,0.35) !important;
    box-shadow: 0 14px 30px rgba(120,160,240,0.15) !important;
}

/* 기본(primary) 버튼 */
button[kind="primary"], [data-testid="stBaseButton-primary"] {
    border-radius: 16px !important;
    background: linear-gradient(135deg, #4f8bf9, #6aa1ff) !important;
    font-weight: 800 !important; color: white !important; border: none !important;
    box-shadow: 0 10px 20px rgba(79,139,249,0.4) !important;
    transition: all 0.2s ease !important;
}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 14px 28px rgba(79,139,249,0.5) !important;
}
button, [data-testid="stBaseButton-secondary"] { border-radius: 14px !important; }

/* 다운로드 버튼도 블루로 */
[data-testid="stDownloadButton"] button {
    border-radius: 16px !important;
    background: linear-gradient(135deg, #4f8bf9, #6aa1ff) !important;
    color: white !important; font-weight: 700 !important; border: none !important;
    box-shadow: 0 8px 18px rgba(79,139,249,0.35) !important;
}

/* 탭 */
button[role="tab"] { font-weight: 700 !important; padding: 0.5rem 1rem !important; }
button[aria-selected="true"] {
    color: #1d4ed8 !important; border-bottom: 3px solid #6aa1ff !important;
}

/* 데이터프레임 라운드 */
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    border-radius: 16px !important; overflow: hidden;
    box-shadow: 0 8px 20px rgba(120,160,240,0.12);
}

/* 사이드바 */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #e6f0ff, #dbe8ff);
}
</style>

<div class="hero">
  <h1>💼 다우오피스 광고 리포트</h1>
  <p>다우오피스 · 다우오피스HR 광고 RAW 생성기</p>
</div>
""", unsafe_allow_html=True)


# ── secrets 안전 접근 ──────────────────────────────────────
def get_secrets():
    try:
        return dict(st.secrets)
    except Exception:
        return {}


SECRETS = get_secrets()


# ── 소스 config 조회 헬퍼 ──────────────────────────────────
def ad_source(label):
    return next(s for s in AD_SOURCES if s["label"] == label)


# ── xlsx 내보내기 ──────────────────────────────────────────
def build_xlsx(raw_df, check):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        raw_df.to_excel(writer, sheet_name="RAW", index=False)
        # 데이터 점검 시트: 섹션 제목 + 표를 순서대로 쌓기
        ws_rows = []
        for sec in check["sections"]:
            ws_rows.append([sec["title"]])
            df = sec["df"]
            ws_rows.append(list(df.columns))
            for _, r in df.iterrows():
                ws_rows.append(list(r))
            ws_rows.append([])
        maxw = max((len(r) for r in ws_rows), default=1)
        norm = [r + [""] * (maxw - len(r)) for r in ws_rows]
        pd.DataFrame(norm).to_excel(writer, sheet_name="데이터 점검", index=False, header=False)
    buf.seek(0)
    return buf.getvalue()


# ── RAW 생성 실행 ──────────────────────────────────────────
def run_generation(since, until, naver_file, saramin_file, ga_files=None):
    logs = []
    ad_data = []
    ga_files = ga_files or {}

    # 네이버 : API 자격증명 있으면 API, 없으면 업로드 파일
    if naver_ok(SECRETS):
        nv_recs, logs = get_naver_api_rows(SECRETS, since, until, logs)
        ad_data.append({"source": ad_source("네이버"), "records": nv_recs, "found": True})
    else:
        nv_recs, logs = parse_upload(naver_file, ad_source("네이버")["col"], logs) if naver_file else ([], logs)
        ad_data.append({"source": ad_source("네이버"), "records": nv_recs, "found": naver_file is not None})

    # 구글 (API)
    g_recs, logs = get_google_rows(since, until, SECRETS, logs)
    ad_data.append({"source": ad_source("구글"), "records": g_recs, "found": google_ok(SECRETS)})

    # 메타 (API)
    m_recs, logs = get_meta_rows(since, until, SECRETS, logs)
    ad_data.append({"source": ad_source("메타"), "records": m_recs, "found": meta_ok(SECRETS)})

    # 사람인 (업로드)
    sr_recs, logs = parse_upload(saramin_file, ad_source("사람인")["col"], logs) if saramin_file else ([], logs)
    ad_data.append({"source": ad_source("사람인"), "records": sr_recs, "found": saramin_file is not None})

    # GA 파일 자동 판별: 슬롯이 바뀌어 올라와도 내용(직원수 컬럼=HR)으로 브랜드에 맞춘다.
    from src.sources.ga_upload import detect_brand
    slot_map = {"GA_DO": "DO", "GA_HR": "HR"}
    ga_by_brand = {}
    for slot, gfile in ga_files.items():
        if gfile is None:
            continue
        b = detect_brand(gfile) or slot_map.get(slot)
        expected = slot_map.get(slot)
        if b != expected:
            logs.append(f"⚠️ GA 파일 슬롯 자동보정: '{slot}' 칸의 파일이 {b} 데이터로 판별되어 {b}로 처리합니다.")
        ga_by_brand[b] = gfile

    # GA : 파일이 있으면 파일 우선, 없으면 GA4 API
    ga_data = []
    for src in GA_SOURCES:
        gfile = ga_by_brand.get(src["brand"])
        if gfile is not None:
            recs, logs = parse_ga_upload(gfile, src, logs)
            ga_data.append({"source": src, "records": recs, "found": True})
        else:
            recs, logs = get_ga_records(src, SECRETS, since, until, logs)
            ga_data.append({"source": src, "records": recs, "found": bool(SECRETS.get(src["property_secret"]))})

    # 매핑표 로드
    media_index = ms.build_media_index_map(ms.load_table("media_index"))
    ga_index = ms.build_ga_index_map(ms.load_table("ga_index"))
    meta_map = ms.build_meta_content_map(ms.load_table("meta_map"))
    brand_search = ms.build_brand_search_contracts(ms.load_table("brand_search"))

    result = run_pipeline(ad_data, ga_data, media_index, ga_index, meta_map,
                          brand_search=brand_search, since=since, until=until)
    raw_df = pd.DataFrame(result["rows"], columns=RAW_HEADERS)
    check = build_check_report(result["stats"], meta_map, result["rows"])

    return {
        "raw_df": raw_df, "check": check, "logs": logs,
        "adRowCount": result["adRowCount"], "leftoverCount": result["leftoverCount"],
    }


# ── 날짜 프리셋 콜백 (위젯 생성 전에 session_state 갱신) ──────
def _preset_last7():
    today = date.today()
    e = today - timedelta(days=1)
    st.session_state["since_date"] = e - timedelta(days=6)
    st.session_state["until_date"] = e


def _preset_lastweek():
    today = date.today()
    this_mon = today - timedelta(days=today.weekday())
    last_mon = this_mon - timedelta(days=7)
    st.session_state["since_date"] = last_mon
    st.session_state["until_date"] = last_mon + timedelta(days=6)


# ── 사이드바 ───────────────────────────────────────────────
with st.sidebar:
    st.header("📊 다우오피스 리포트")
    today = date.today()
    if "since_date" not in st.session_state:
        st.session_state["since_date"] = today - timedelta(days=14)
    if "until_date" not in st.session_state:
        st.session_state["until_date"] = today - timedelta(days=1)

    since = st.date_input("시작일", key="since_date")
    until = st.date_input("종료일", key="until_date")

    b1, b2 = st.columns(2)
    b1.button("최근 7일", on_click=_preset_last7, use_container_width=True)
    b2.button("지난주(월~일)", on_click=_preset_lastweek, use_container_width=True)

    st.divider()
    st.caption("연동 상태")
    st.write(("✅ " if google_ok(SECRETS) else "⚪ ") + "구글 Ads API")
    st.write(("✅ " if meta_ok(SECRETS) else "⚪ ") + "메타 API")
    st.write(("✅ " if naver_ok(SECRETS) else "⚪ ") + "네이버 검색광고 API")
    st.write(("✅ " if ga4_ok(SECRETS) else "⚪ ") + "GA4 Data API")
    st.caption("네이버는 API 없으면 업로드 · 사람인은 원본 업로드")


tab_raw, tab_check, tab_map = st.tabs(["RAW 생성", "데이터 점검", "매핑 관리"])

# ── 탭: RAW 생성 ───────────────────────────────────────────
with tab_raw:
    st.subheader("RAW 생성 (취합 + GA 결합)")
    _naver_api = naver_ok(SECRETS)
    _ga_api = ga4_ok(SECRETS)
    st.caption(
        ("네이버 " + ("API" if _naver_api else "업로드"))
        + " · 사람인 업로드 · 구글·메타 API · GA "
        + ("API" if _ga_api else "업로드")
    )

    naver_file = None
    if _naver_api:
        c1 = st.container()
        with c1:
            saramin_file = st.file_uploader("사람인 원본 (엑셀/CSV/TSV)", type=["xlsx", "xls", "csv", "tsv", "txt"], key="sr")
    else:
        c1, c2 = st.columns(2)
        with c1:
            naver_file = st.file_uploader("네이버 원본 (엑셀/CSV/TSV)", type=["xlsx", "xls", "csv", "tsv", "txt"], key="nv")
        with c2:
            saramin_file = st.file_uploader("사람인 원본 (엑셀/CSV/TSV)", type=["xlsx", "xls", "csv", "tsv", "txt"], key="sr")

    st.markdown("**GA 원본 업로드** (선택 — 넣으면 GA4 API 대신 이 파일을 사용)")
    g1, g2 = st.columns(2)
    with g1:
        ga_do_file = st.file_uploader("GA_DO — 다우오피스 (엑셀/CSV/TSV)", type=["xlsx", "xls", "csv", "tsv", "txt"], key="ga_do")
    with g2:
        ga_hr_file = st.file_uploader("GA_HR — 다우오피스HR (엑셀/CSV/TSV)", type=["xlsx", "xls", "csv", "tsv", "txt"], key="ga_hr")

    if st.button("🚀 RAW 생성", type="primary"):
        with st.spinner("취합 + GA 결합 중..."):
            try:
                st.session_state["result"] = run_generation(
                    since.strftime("%Y-%m-%d"), until.strftime("%Y-%m-%d"),
                    naver_file, saramin_file,
                    ga_files={"GA_DO": ga_do_file, "GA_HR": ga_hr_file},
                )
            except Exception as e:
                st.session_state["result"] = None
                st.error(f"실행 오류: {e}")
                st.exception(e)

    res = st.session_state.get("result")
    if res:
        ck = res["check"]
        msg = (f"완료 — 총 {len(res['raw_df'])}행 "
               f"(광고 {res['adRowCount']} / GA 미매칭 {res['leftoverCount']})")
        if ck["pass"]:
            st.success(msg + " · 점검 이상 없음")
        else:
            st.warning(msg + f" · 점검 확인 항목 {ck['issues']}건 (‘데이터 점검’ 탭 확인)")

        st.download_button(
            "⬇️ RAW.xlsx 다운로드",
            data=build_xlsx(res["raw_df"], ck),
            file_name=f"다우오피스_RAW_{since:%y%m%d}_{until:%y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.dataframe(res["raw_df"], use_container_width=True, height=520)

        with st.expander("실행 로그"):
            st.code("\n".join(res["logs"]) or "(로그 없음)")
    else:
        st.info("기간을 고르고 필요한 원본을 업로드한 뒤 ‘RAW 생성’을 누르세요.")

# ── 탭: 데이터 점검 ────────────────────────────────────────
with tab_check:
    st.subheader("데이터 점검 리포트")
    res = st.session_state.get("result")
    if not res:
        st.info("먼저 ‘RAW 생성’ 탭에서 생성을 실행하세요.")
    else:
        ck = res["check"]
        if ck["pass"]:
            st.success("점검 결과: 이상 없음")
        else:
            st.error(f"점검 결과: 확인 항목 {ck['issues']}건")
        for sec in ck["sections"]:
            icon = "✅" if sec["pass"] else "❌"
            st.markdown(f"**{icon} {sec['title']}**")
            st.dataframe(sec["df"], use_container_width=True, hide_index=True)

# ── 탭: 매핑 관리 ──────────────────────────────────────────
with tab_map:
    st.subheader("매핑표 관리")
    st.caption("편집 후 ‘저장’을 눌러야 반영됩니다. 엑셀 업로드로 전체 교체도 가능합니다.")

    def mapping_editor(key, title, help_text):
        st.markdown(f"### {title}")
        st.caption(help_text)
        up = st.file_uploader(f"{title} 엑셀/CSV/TSV 업로드(전체 교체)", type=["xlsx", "xls", "csv", "tsv", "txt"], key=f"up_{key}")
        if up is not None:
            try:
                nm = up.name.lower()
                if nm.endswith((".csv", ".tsv", ".txt")):
                    sep = "\t" if nm.endswith((".tsv", ".txt")) else None  # None=자동감지
                    try:
                        new_df = pd.read_csv(up, dtype=str, sep=sep, engine="python").fillna("")
                    except UnicodeDecodeError:
                        up.seek(0)
                        new_df = pd.read_csv(up, dtype=str, sep=sep, engine="python", encoding="cp949").fillna("")
                else:
                    new_df = pd.read_excel(up, dtype=str).fillna("")
                ms.save_table(key, new_df)
                st.success(f"{title} 업로드 저장 완료")
            except Exception as e:
                st.error(f"업로드 실패: {e}")

        df = ms.load_table(key)
        edited = st.data_editor(df, num_rows="dynamic", use_container_width=True, key=f"ed_{key}")
        if st.button(f"💾 {title} 저장", key=f"save_{key}"):
            ms.save_table(key, edited)
            st.success(f"{title} 저장 완료")

    sub1, sub2, sub3, sub4 = st.tabs(["매체 INDEX", "GA INDEX", "meta 정리", "고정비(브검·사람인)"])
    with sub1:
        mapping_editor("media_index", "매체 INDEX",
                       "캠페인 → 브랜드·구분. 광고 캠페인명을 브랜드/구분으로 분류합니다.")
    with sub2:
        mapping_editor("ga_index", "GA INDEX",
                       "브랜드 + 세션 소스/매체 → 구분·매체·디바이스. 소스/매체 목록이 화이트리스트 역할도 합니다.")
    with sub3:
        mapping_editor("meta_map", "meta 정리",
                       "캠페인 + 광고세트 + 광고이름 → ga컨텐츠. 메타 광고의 GA 결합키를 지정합니다.")
    with sub4:
        mapping_editor("brand_search", "고정비 계약 (브랜드검색·사람인 등)",
                       "클릭과금이 아닌 정액 계약비(네이버 브랜드검색, 사람인 배너 등). 계약 기간"
                       "(시작일~종료일) 동안 매일 '일일광고비'가 지정한 (브랜드·매체·디바이스·광고이름) "
                       "행의 광고비로 채워집니다. 해당 행이 없는 날은 새 행이 추가됩니다. "
                       "기간을 비우면 조회기간 전체에 적용됩니다.")
