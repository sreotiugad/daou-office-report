"""
설정값 — GAS "RAW 생성기 v5"의 1절(CONFIG)을 그대로 포팅.

원본 GAS는 구글시트 시트명/열 인덱스를 참조했지만, 여기서는
- 광고 원본: 구글·메타는 API 실시간, 네이버·사람인은 업로드 엑셀
- 매핑표(매체 INDEX / GA INDEX / meta 정리)는 Streamlit 안에서 편집
로 바뀌었으므로, "결합 로직에 필요한 설정"만 남기고 시트 좌표 설정은 뺐다.
"""

# ── 출력 스키마 ─────────────────────────────────────────────
RAW_HEADERS = [
    "브랜드", "구분", "매체", "디바이스", "날짜", "캠페인",
    "광고그룹", "광고이름", "노출", "클릭", "광고비",
    "평균노출순위", "조회", "GA 전환수", "직원수",
]

# 미매칭 GA 행의 광고그룹 열 표시값
LEFTOVER_TAG = "GA 미매칭"

# 콘텐츠가 (not set) 인 GA 전환도 하단에 추가할지 여부
INCLUDE_NOSET_IN_LEFTOVER = False

# 키가 겹치는 광고 행이 2개 이상일 때 전환 배분 방식
#  "top_click" : 클릭이 가장 많은 행에만 전환을 넣는다 (합계 보존, 정수 유지) ← 권장
#  "split"     : 클릭수 비율로 나눈다 (합계 보존, 소수점 발생)
#  "all"       : 모든 행에 그대로 표시한다 (합계가 부풀 수 있음)
DUP_MODE = "top_click"

# 광고비 배수 적용 후 원 단위로 반올림할지 여부
ROUND_COST = True

UNCLASSIFIED = "미분류"

# ── 브랜드 보정 규칙 ────────────────────────────────────────
#  매체 INDEX로 정해진 브랜드를 특정 조건에서 덮어쓴다.
#  예) 구분이 BSA 인데 광고그룹에 "hr" 이 들어가면 → 브랜드를 HR 로 변경
BRAND_OVERRIDE_RULES = [
    {"whenGubun": "BSA", "adGroupContains": "hr", "setBrand": "HR"},
]

# ── 광고 소스 정의 ──────────────────────────────────────────
#  네이버·사람인은 업로드(kind="upload"), 구글·메타는 API(kind="api").
#  device 가 문자열이면 고정값, None 이면 원본 컬럼에서 읽음.
#  col 의 값은 업로드 파서가 읽을 "열 이름 후보" 목록. (엑셀 헤더가 매번
#  조금씩 달라서 여러 후보를 두고 첫 매칭을 쓴다.)
#  useMetaMap : [meta 정리]에서 GA 결합키(ga컨텐츠)를 조회한다.
#  filterKeyword : 캠페인명에 이 문자열이 포함된 행만 사용.
AD_SOURCES = [
    {
        "label": "네이버",
        "kind": "upload",
        "device": None,          # 원본에서 읽음
        "col": {
            "device":   ["디바이스", "기기", "매체(광고상품)", "pcMblTp"],
            "date":     ["날짜", "일자", "기간", "statDt"],
            "campaign": ["캠페인", "캠페인명"],
            "adGroup":  ["광고그룹", "광고그룹명", "그룹"],
            "adName":   ["광고이름", "키워드", "소재", "광고소재"],
            "imp":      ["노출", "노출수", "노출 수"],
            "click":    ["클릭", "클릭수", "클릭 수"],
            "cost":     ["광고비", "비용", "총비용", "총 비용"],
            "rank":     ["평균노출순위", "평균 노출순위", "노출순위", "평균순위"],
            "view":     None,
        },
    },
    {
        "label": "구글",
        "kind": "api",
        "device": None,          # API segments.device
        "costMultiplier": 1.1,   # 부가세 포함 (광고비 × 1.1)
    },
    {
        "label": "메타",
        "kind": "api",
        "device": "모바일",       # GAS 설정 그대로 고정
        "useMetaMap": True,
        "costMultiplier": 1.1,   # 부가세 포함 (광고비 × 1.1)
    },
    {
        "label": "사람인",
        "kind": "upload",
        "device": "PC",          # GAS 설정 그대로 고정
        "filterKeyword": "다우오피스",
        "col": {
            "date":     ["날짜", "일자", "기간"],
            "campaign": ["캠페인", "캠페인명", "상품명"],
            "adGroup":  ["광고그룹", "그룹", "광고그룹명"],
            "adName":   ["광고이름", "키워드", "소재"],
            "imp":      ["노출", "노출수", "노출 수"],
            "click":    ["클릭", "클릭수", "클릭 수"],
            "cost":     None,    # 사람인 원본엔 비용 없음
            "rank":     None,
            "view":     None,
        },
    },
]

# ── GA 소스 정의 ────────────────────────────────────────────
#  GAS 원본은 GA_DO / GA_HR 시트를 읽었으나, 여기서는 GA4 Data API로 직접 조회.
#  property_secret : secrets.toml 의 GA4 속성 ID 키 이름
#  has_emp         : 직원수(다우오피스HR 전용) 지표 존재 여부
GA_SOURCES = [
    {"sheet": "GA_DO", "brand": "DO", "property_secret": "GA4_PROPERTY_DO", "has_emp": False},
    {"sheet": "GA_HR", "brand": "HR", "property_secret": "GA4_PROPERTY_HR", "has_emp": True},
]

# ── GA4 지표/측정기준 매핑 ──────────────────────────────────
#  ⚠️ 아래 값들은 실제 GA4 속성 설정을 보고 채워야 정확하다.
#  - conversion_event : "전환수"로 집계할 GA4 키 이벤트 이름
#  - employee_metric  : 다우오피스HR "직원수"에 해당하는 커스텀 측정항목/이벤트
#  - content_dimension: GAS의 "콘텐츠(광고이름)" 결합키에 대응하는 GA4 측정기준
#  - medium_dimension : "세션 소스/매체"
GA4_SETTINGS = {
    "conversion_event": "sign_up",          # TODO: 실제 키 이벤트명으로 교체
    "employee_metric": None,                 # TODO: 직원수 커스텀 지표명 (없으면 None)
    "content_dimension": "sessionManualAdContent",   # 세션 콘텐츠(utm_content)
    "medium_dimension": "sessionSourceMedium",       # 세션 소스/매체
    "campaign_dimension": "sessionCampaignName",
    "date_dimension": "date",
}

# 광고와 매칭할 수 없는 GA 콘텐츠 값
GA_SKIP_CONTENT = ["", "(not set)", "(not provided)"]
