/**
 * 사람인 보고서 메일 → 매핑 구글시트 [saramin_raw] 탭 자동 적재
 *
 * Saramin AdsCenter(noreply@nestads.com)가 매주 월요일·매월 1일 10시에 보내는
 * "다우오피스_ 사람인_리포트_YYYYMMDD.xlsx" 첨부를 읽어, 일별 행만
 * [saramin_raw] 탭에 넣는다. (일자·캠페인·광고그룹·광고) 가 같은 행은
 * 더 최근 보고서 값으로 덮어써서 중복 없이 쌓인다.
 * 보고서 하단의 '합계' 행과 기간합계 행(일자 "A~B")은 넣지 않는다.
 *
 * 설치(1회) — README "사람인 메일 자동 적재" 참고
 *   1) script.google.com → 새 프로젝트 → 이 파일 내용 붙여넣기
 *   2) 왼쪽 '서비스 +' → Drive API 추가 (xlsx → 시트 변환용)
 *   3) backfill() 한 번 실행 → 권한 허용 (최근 메일 일괄 적재)
 *   4) installTrigger() 한 번 실행 → 매일 11시 자동 실행 등록
 */

// 매핑 구글시트 ID (앱 secrets 의 MAPPING_SHEET_ID 와 같은 값, 주소 /d/<ID>/edit 의 <ID>)
// repo 가 공개라 실제 값은 Apps Script 에 붙여넣은 뒤에만 채운다.
const SHEET_ID = 'PUT_MAPPING_SHEET_ID_HERE';
const TAB = 'saramin_raw';
const QUERY = 'from:noreply@nestads.com subject:"사람인_리포트" has:attachment';
const DONE_LABEL = '사람인RAW적재';
const HEADERS = ['일자', '광고주명', '캠페인명', '광고그룹명', '광고명', '노출수', '클릭수', '보고서일', '적재시각'];

/** 매일 트리거가 부르는 함수: 아직 적재 안 한 메일만 처리 */
function run() {
  importMail_(QUERY + ' -label:' + DONE_LABEL + ' newer_than:14d');
}

/** 최초 1회: 최근 120일 메일 전부 적재 */
function backfill() {
  importMail_(QUERY + ' newer_than:120d');
}

/** 매일 11시(한국시간) 실행 트리거 등록. 이미 있으면 다시 만들지 않음 */
function installTrigger() {
  const exists = ScriptApp.getProjectTriggers().some(t => t.getHandlerFunction() === 'run');
  if (!exists) {
    ScriptApp.newTrigger('run').timeBased().everyDays(1).atHour(11).inTimezone('Asia/Seoul').create();
  }
}

function importMail_(query) {
  const label = GmailApp.getUserLabelByName(DONE_LABEL) || GmailApp.createLabel(DONE_LABEL);
  const threads = GmailApp.search(query, 0, 100);
  if (!threads.length) {
    console.log('새 사람인 보고서 메일 없음');
    return;
  }

  // 보고서일 오래된 것부터 처리해야 최신 값이 마지막에 덮어씀
  const reports = [];
  const seen = {};
  threads.forEach(th => th.getMessages().forEach(msg => {
    msg.getAttachments().forEach(att => {
      const name = att.getName();
      const m = name.match(/(\d{8})\.xlsx$/);
      if (!m || seen[name]) return; // 같은 메일이 그룹 2곳으로 와서 첨부가 중복됨
      seen[name] = true;
      reports.push({ reportDate: m[1], blob: att.copyBlob() });
    });
  }));
  reports.sort((a, b) => a.reportDate.localeCompare(b.reportDate));

  const sh = openTab_();
  const store = readStore_(sh);
  const now = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm');
  let added = 0;
  reports.forEach(rep => {
    const rows = readXlsx_(rep.blob);
    rows.forEach(r => {
      const key = [r[0], r[2], r[3], r[4]].join('\t');
      if (!store.map[key]) { store.order.push(key); added++; }
      store.map[key] = r.concat([fmtReportDate_(rep.reportDate), now]);
    });
    console.log('보고서 ' + rep.reportDate + ': 일별 ' + rows.length + '행');
  });

  writeStore_(sh, store);
  threads.forEach(th => th.addLabel(label));
  console.log('완료: 보고서 ' + reports.length + '개, 신규 행 ' + added + ', 전체 ' + store.order.length);
}

/** xlsx 첨부를 임시 구글시트로 변환해 일별 행만 [일자, 광고주명, 캠페인명, 광고그룹명, 광고명, 노출수, 클릭수] 로 반환 */
function readXlsx_(blob) {
  const tmp = Drive.Files.create({ name: 'tmp_saramin_' + Date.now(), mimeType: MimeType.GOOGLE_SHEETS }, blob);
  try {
    const values = SpreadsheetApp.openById(tmp.id).getSheets()[0].getDataRange().getDisplayValues();
    const h = values.findIndex(row => row.indexOf('광고주명') >= 0 && row.indexOf('일자') >= 0);
    if (h < 0) throw new Error('헤더(광고주명/일자) 행을 찾지 못함');
    const col = name => values[h].indexOf(name);
    const c = {
      date: col('일자'), adv: col('광고주명'), cmp: col('캠페인명'), grp: col('광고그룹명'),
      ad: col('광고명'), imp: col('노출수'), clk: col('클릭수'),
    };
    const out = [];
    for (let i = h + 1; i < values.length; i++) {
      const row = values[i];
      const date = String(row[c.date] || '').trim();
      if (row[c.adv] === '합계' || !/^\d{4}-\d{2}-\d{2}$/.test(date)) continue; // 합계·기간합계(A~B) 제외
      out.push([date, row[c.adv], row[c.cmp], row[c.grp], row[c.ad], num_(row[c.imp]), num_(row[c.clk])]);
    }
    return out;
  } finally {
    DriveApp.getFileById(tmp.id).setTrashed(true);
  }
}

function openTab_() {
  const ss = SpreadsheetApp.openById(SHEET_ID);
  let sh = ss.getSheetByName(TAB);
  if (!sh) {
    sh = ss.insertSheet(TAB);
    sh.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
    sh.setFrozenRows(1);
  }
  return sh;
}

function readStore_(sh) {
  const map = {}, order = [];
  const last = sh.getLastRow();
  if (last > 1) {
    sh.getRange(2, 1, last - 1, HEADERS.length).getDisplayValues().forEach(r => {
      const key = [r[0], r[2], r[3], r[4]].join('\t');
      if (!map[key]) order.push(key);
      map[key] = r;
    });
  }
  return { map, order };
}

function writeStore_(sh, store) {
  const rows = store.order.map(k => store.map[k]);
  rows.sort((a, b) => (a[0] + a[2] + a[3] + a[4]).localeCompare(b[0] + b[2] + b[3] + b[4]));
  sh.getRange(2, 1, Math.max(sh.getLastRow() - 1, 1), HEADERS.length).clearContent();
  if (rows.length) {
    sh.getRange(2, 1, rows.length, 1).setNumberFormat('@'); // 일자를 문자열로 유지
    sh.getRange(2, 1, rows.length, HEADERS.length).setValues(rows);
  }
}

function num_(v) {
  const n = Number(String(v).replace(/,/g, ''));
  return isNaN(n) ? 0 : n;
}

function fmtReportDate_(d) {
  return d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6, 8);
}
