# 다우오피스 광고 리포트 (Streamlit)

사방넷 Google Apps Script "RAW 생성기 v5"의 로직을 그대로 웹앱으로 옮긴 것입니다.
광고 원본(네이버·구글·META·사람인)을 취합하고 GA4 전환수를 6중 키
(브랜드·구분·매체·디바이스·날짜·광고이름)로 결합해 `RAW` 시트와 `데이터 점검`을 만듭니다.

## 데이터 소스

| 소스 | 방식 |
|------|------|
| 구글 | Google Ads API 실시간 (키워드 레벨) |
| 메타 | Meta Graph API 실시간 (광고 레벨, 디바이스 모바일 고정) |
| 네이버 | 엑셀/CSV 업로드 |
| 사람인 | 엑셀/CSV 업로드 (캠페인명에 "다우오피스" 포함 행만, 디바이스 PC 고정) |
| GA 전환수·직원수 | GA4 Data API (다우오피스 / 다우오피스HR 속성) |

매체 INDEX / GA INDEX / meta 정리 매핑표는 앱의 **매핑 관리** 탭에서 편집·업로드하며
`data/*.csv`로 저장됩니다.

## 로컬 실행

```bash
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml   # 후 실제 값 입력
streamlit run streamlit_app.py
```

자격증명이 없어도 **업로드 파싱**과 **매핑 관리**는 동작합니다.
구글·메타·GA4는 secrets가 채워진 것만 조회합니다.

## API 자격증명 설정

### 구글 Ads / 메타
기존 사방넷 리포트에서 쓰던 값과 동일한 형식입니다.
`.streamlit/secrets.toml`의 `GADS_*`, `META_*` 항목을 채우세요.
`GADS_CUSTOMER_IDS`에는 다우오피스에서 조회할 계정 ID를 콤마로 나열합니다.

### GA4 Data API (직접 연동)
GA4 전환수를 API로 바로 가져오려면 아래 준비가 필요합니다. 코드는 이미 되어 있고,
자격증명/속성 설정만 본인 GCP·GA4 계정에서 진행하면 됩니다.

1. **Google Cloud Console** → 프로젝트 선택 → "API 및 서비스" → **Google Analytics Data API** 사용 설정
2. "사용자 인증 정보" → **서비스 계정 만들기** → 키 유형 JSON으로 **키 다운로드**
3. **GA4 관리** → (다우오피스 속성) → "속성 액세스 관리" → 서비스 계정 이메일
   (`xxx@xxx.iam.gserviceaccount.com`)을 **뷰어**로 추가.
   다우오피스HR 속성에도 동일하게 추가.
4. **GA4 관리** → "속성 설정"에서 숫자 **속성 ID** 확인 → `GA4_PROPERTY_DO`, `GA4_PROPERTY_HR`에 입력
5. 다운로드한 JSON 전체를 `GA4_SERVICE_ACCOUNT_JSON`에 붙여넣기

#### ⚠️ GA4 지표/측정기준 이름 확인 (필수)
`src/config.py`의 `GA4_SETTINGS`에서 아래 값을 실제 GA4 속성에 맞게 조정하세요.
속성마다 이름이 다를 수 있어 코드에 하드코딩하지 않고 설정으로 뺐습니다.

- `conversion_event` : "전환수"로 셀 GA4 **키 이벤트 이름** (예: `sign_up`)
- `employee_metric` : 다우오피스HR "직원수"에 해당하는 커스텀 지표명 (없으면 `None`)
- `content_dimension` : 광고이름 결합키로 쓸 측정기준 (기본 `sessionManualAdContent` = utm_content)
- `medium_dimension` : 세션 소스/매체 (기본 `sessionSourceMedium`)

GA4 탐색 보고서에서 어떤 이벤트/측정기준을 쓰는지 확인한 뒤 위 값을 맞추면 됩니다.

## Streamlit Community Cloud 배포

1. 이 폴더를 GitHub repo로 push
2. [share.streamlit.io](https://share.streamlit.io) 로그인 → "New app" → 해당 repo·`streamlit_app.py` 선택
3. **Settings → Secrets**에 `.streamlit/secrets.toml` 내용을 그대로 붙여넣기
4. Deploy

## 프로젝트 구조

```
daou-office-report/
├── streamlit_app.py        # 엔트리포인트 (RAW 생성 / 데이터 점검 / 매핑 관리 탭)
├── src/
│   ├── config.py           # 스키마·소스·GA4 설정 (GAS 1절)
│   ├── helpers.py          # 정규화·결합키 (GAS 3절)
│   ├── mapping_store.py    # 매핑표 CSV 저장·룩업 변환
│   ├── pipeline.py         # 취합·GA 결합 (GAS 7~10절)
│   ├── validate.py         # 데이터 점검 리포트 (GAS 12절)
│   ├── sources/            # google_ads / meta_ads / upload(네이버·사람인)
│   └── ga4/client.py       # GA4 Data API 래퍼
└── data/                   # 매핑 CSV (gitignore)
```
