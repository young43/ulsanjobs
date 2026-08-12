# -*- coding: utf-8 -*-
"""
data/jobs.json 을 읽어 검색·필터 가능한 채용정보 사이트를 만든다.

  site/index.html    로컬에서 바로 열어보는 완성 HTML
  site/artifact.html 아티팩트 발행용(문서 골격 없이 본문만)

사용법: python build_site.py
"""
from __future__ import annotations

import io
import json
import os
import sys
from collections import Counter
from datetime import datetime

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "jobs.json")
SITE = os.path.join(HERE, "site")

# 고용형태 -> 배지 색 클래스
BADGE = {
    "무기계약직": "b-perm",
    "공무직": "b-perm",
    "기간제·계약직": "b-term",
    "교원·강사·연구직": "b-edu",
    "공무원(임기제·개방형)": "b-gov",
    "정규직": "b-full",
    "기타": "b-etc",
}

CSS = """
*, *::before, *::after { box-sizing: border-box; }

:root {
  --bg:        #f5f7f9;
  --surface:   #ffffff;
  --surface-2: #eceff4;
  --border:    #dce2ea;
  --border-2:  #c7d0da;
  --text:      #16202b;
  --text-dim:  #5a6a7b;
  --text-faint:#8794a3;
  --accent:    #0e6a70;
  --accent-2:  #0a4f54;
  --accent-bg: #dceceb;
  --on-accent: #ffffff;
  --warn:      #9a5209;
  --warn-bg:   #fbeada;
  --shadow:    0 1px 2px rgba(20,32,44,.06), 0 4px 14px rgba(20,32,44,.05);

  --perm:   #0e6a70;  --perm-bg:   #d7eceb;
  --term:   #1f5b96;  --term-bg:   #dbe8f6;
  --edu:    #6b3f96;  --edu-bg:    #e9dff5;
  --gov:    #4a5568;  --gov-bg:    #e2e6ec;
  --full:   #2f6b34;  --full-bg:   #ddeedd;
  --etc:    #6b7280;  --etc-bg:    #e6e8ec;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:        #0f141a;
    --surface:   #161d25;
    --surface-2: #1d2732;
    --border:    #27323e;
    --border-2:  #35434f;
    --text:      #e5ecf3;
    --text-dim:  #93a3b4;
    --text-faint:#6f7f8f;
    --accent:    #45b7bb;
    --accent-2:  #6fd0d3;
    --accent-bg: #10353a;
    --on-accent: #062224;
    --warn:      #e2a662;
    --warn-bg:   #37281353;
    --shadow:    0 1px 2px rgba(0,0,0,.4), 0 4px 16px rgba(0,0,0,.3);

    --perm:  #4fbec2;  --perm-bg:  #10353a;
    --term:  #79b0ea;  --term-bg:  #15293f;
    --edu:   #b795e0;  --edu-bg:   #2a1f3c;
    --gov:   #9aa8ba;  --gov-bg:   #222b36;
    --full:  #7cc182;  --full-bg:  #17301a;
    --etc:   #97a2af;  --etc-bg:   #222932;
  }
}

:root[data-theme="dark"] {
  --bg:        #0f141a;
  --surface:   #161d25;
  --surface-2: #1d2732;
  --border:    #27323e;
  --border-2:  #35434f;
  --text:      #e5ecf3;
  --text-dim:  #93a3b4;
  --text-faint:#6f7f8f;
  --accent:    #45b7bb;
  --accent-2:  #6fd0d3;
  --accent-bg: #10353a;
  --on-accent: #062224;
  --warn:      #e2a662;
  --warn-bg:   #37281353;
  --shadow:    0 1px 2px rgba(0,0,0,.4), 0 4px 16px rgba(0,0,0,.3);

  --perm:  #4fbec2;  --perm-bg:  #10353a;
  --term:  #79b0ea;  --term-bg:  #15293f;
  --edu:   #b795e0;  --edu-bg:   #2a1f3c;
  --gov:   #9aa8ba;  --gov-bg:   #222b36;
  --full:  #7cc182;  --full-bg:  #17301a;
  --etc:   #97a2af;  --etc-bg:   #222932;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Pretendard Variable", Pretendard, -apple-system, BlinkMacSystemFont,
               "Apple SD Gothic Neo", "Malgun Gothic", "맑은 고딕", "Noto Sans KR",
               system-ui, sans-serif;
  font-size: 15px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

.wrap { max-width: 1120px; margin: 0 auto; padding: 0 20px 72px; }

/* ---------- 헤더 ---------- */
.masthead {
  padding: 40px 0 22px;
  border-bottom: 1px solid var(--border);
  display: flex; flex-wrap: wrap; align-items: flex-end;
  justify-content: space-between; gap: 16px;
}
.eyebrow {
  font-size: 11px; font-weight: 700; letter-spacing: .16em;
  text-transform: uppercase; color: var(--accent); margin: 0 0 8px;
}
h1 { font-size: 29px; line-height: 1.22; margin: 0; letter-spacing: -.02em; text-wrap: balance; }
.sub { margin: 9px 0 0; color: var(--text-dim); font-size: 14px; max-width: 60ch; }
.updated {
  font-size: 12.5px; color: var(--text-faint); text-align: right;
  font-variant-numeric: tabular-nums; white-space: nowrap;
}

/* ---------- 요약 ---------- */
.stats {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 12px; margin: 22px 0 26px;
}
.stat {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px; box-shadow: var(--shadow);
}
.stat .n {
  font-size: 26px; font-weight: 700; letter-spacing: -.02em;
  font-variant-numeric: tabular-nums; display: block; line-height: 1.15;
}
.stat .k { font-size: 12px; color: var(--text-dim); margin-top: 3px; display: block; }
.stat.hi .n { color: var(--accent); }
.stat.warn .n { color: var(--warn); }

/* ---------- 필터 ---------- */
.filters {
  position: sticky; top: 0; z-index: 20;
  background: var(--bg); border-bottom: 1px solid var(--border);
  padding: 12px 0 13px; margin-bottom: 4px;
}
.frow { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.frow + .frow { margin-top: 9px; }

input[type="search"], select {
  font: inherit; font-size: 14px; color: var(--text);
  background: var(--surface); border: 1px solid var(--border-2);
  border-radius: 8px; padding: 8px 11px;
}
input[type="search"] { flex: 1 1 260px; min-width: 0; }
select { cursor: pointer; }
input[type="search"]:focus-visible, select:focus-visible,
.chip:focus-visible, .job a:focus-visible, .tbtn:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}

.chip {
  font: inherit; font-size: 13px; cursor: pointer;
  background: var(--surface); color: var(--text-dim);
  border: 1px solid var(--border-2); border-radius: 999px;
  padding: 5px 12px; display: inline-flex; align-items: center; gap: 6px;
  transition: background .12s, color .12s, border-color .12s;
}
.chip:hover { border-color: var(--accent); color: var(--text); }
.chip[aria-pressed="true"] {
  background: var(--accent); border-color: var(--accent);
  color: var(--on-accent); font-weight: 600;
}
.chip .c { font-variant-numeric: tabular-nums; opacity: .75; font-size: 12px; }

.count { font-size: 13px; color: var(--text-dim); margin-left: auto; white-space: nowrap; }
.count b { color: var(--text); font-variant-numeric: tabular-nums; }

/* ---------- 목록 ---------- */
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.job {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 13px 15px 13px 17px;
  display: grid; grid-template-columns: 1fr auto; gap: 4px 18px;
  position: relative; overflow: hidden;
}
.job::before {
  content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--etc);
}
.job.k-perm::before { background: var(--perm); }
.job.k-term::before { background: var(--term); }
.job.k-edu::before  { background: var(--edu); }
.job.k-gov::before  { background: var(--gov); }
.job.k-full::before { background: var(--full); }
.job.closed { opacity: .58; }

.job .t { grid-column: 1; margin: 0; font-size: 15px; font-weight: 600; line-height: 1.45; }
.job a { color: var(--text); text-decoration: none; }
.job a:hover { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.job .meta {
  grid-column: 1; display: flex; flex-wrap: wrap; gap: 5px 12px;
  font-size: 12.5px; color: var(--text-dim); align-items: center;
}
.job .org { font-weight: 500; color: var(--text-dim); }
.job .src { color: var(--text-faint); }
.board-note {
  font-size: 11px; color: var(--text-faint);
  border: 1px dashed var(--border-2); border-radius: 4px; padding: 0 5px;
}

.badge {
  display: inline-block; font-size: 11.5px; font-weight: 700;
  padding: 2px 8px; border-radius: 5px; white-space: nowrap;
  background: var(--etc-bg); color: var(--etc);
}
.b-perm { background: var(--perm-bg); color: var(--perm); }
.b-term { background: var(--term-bg); color: var(--term); }
.b-edu  { background: var(--edu-bg);  color: var(--edu); }
.b-gov  { background: var(--gov-bg);  color: var(--gov); }
.b-full { background: var(--full-bg); color: var(--full); }

.when {
  grid-column: 2; grid-row: 1 / span 2; text-align: right;
  font-size: 12.5px; color: var(--text-dim);
  font-variant-numeric: tabular-nums; white-space: nowrap;
  display: flex; flex-direction: column; align-items: flex-end; gap: 3px;
}
.dday { font-weight: 700; color: var(--accent); }
.dday.soon { color: var(--warn); }
.dday.over { color: var(--text-faint); font-weight: 500; }

.empty { padding: 48px 8px; text-align: center; color: var(--text-dim); }
.more { display: flex; justify-content: center; margin-top: 18px; }
.tbtn {
  font: inherit; font-size: 14px; cursor: pointer; background: var(--surface);
  color: var(--text); border: 1px solid var(--border-2);
  border-radius: 8px; padding: 9px 20px;
}
.tbtn:hover { border-color: var(--accent); color: var(--accent); }

/* ---------- 소스 안내 ---------- */
.sources { margin-top: 52px; border-top: 1px solid var(--border); padding-top: 24px; }
.sources h2 { font-size: 17px; margin: 0 0 4px; letter-spacing: -.01em; }
.sources p { margin: 0 0 16px; color: var(--text-dim); font-size: 13.5px; max-width: 68ch; }
.srcgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(232px, 1fr)); gap: 8px; }
.srcitem {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 9px; padding: 11px 13px;
  display: flex; justify-content: space-between; align-items: baseline; gap: 10px;
}
.srcitem .nm { font-size: 13.5px; font-weight: 600; }
.srcitem .kd { font-size: 11.5px; color: var(--text-faint); display: block; font-weight: 400; margin-top: 1px; }
.srcitem .ct { font-size: 13px; color: var(--accent); font-variant-numeric: tabular-nums; font-weight: 700; }
.foot { margin-top: 26px; font-size: 12.5px; color: var(--text-faint); line-height: 1.7; }

@media (max-width: 720px) {
  .wrap { padding: 0 14px 56px; }
  .masthead { padding: 26px 0 18px; }
  h1 { font-size: 23px; }
  .updated { text-align: left; }
  .stats { grid-template-columns: repeat(2, 1fr); }
  .job { grid-template-columns: 1fr; }
  .when { grid-column: 1; grid-row: auto; text-align: left; flex-direction: row; align-items: center; gap: 10px; }
  .count { margin-left: 0; width: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
"""

JS = """
const RAW = window.__JOBS__;
const jobs = RAW.jobs;
const today = new Date(); today.setHours(0,0,0,0);

const state = { q: "", emp: new Set(), org: "", src: "", open: true, sort: "posted", limit: 60 };

const $ = (s) => document.querySelector(s);
const listEl = $("#list"), countEl = $("#count"), moreEl = $("#more");

function dayDiff(d) {
  const t = new Date(d + "T00:00:00");
  if (isNaN(t)) return null;
  return Math.round((t - today) / 86400000);
}

function isOpen(j) {
  if (!j.deadline || j.deadline === "상시") return true;
  const n = dayDiff(j.deadline);
  return n === null ? true : n >= 0;
}

function match(j) {
  if (state.emp.size && !state.emp.has(j.employment)) return false;
  if (state.org && j.org_type !== state.org) return false;
  if (state.src && j.source !== state.src) return false;
  if (state.open && !isOpen(j)) return false;
  if (state.q) {
    const hay = (j.title + " " + j.org + " " + j.source + " " + j.location + " " + (j.category||"")).toLowerCase();
    if (!state.q.split(/\\s+/).filter(Boolean).every(w => hay.includes(w))) return false;
  }
  return true;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}

function ddayHtml(j) {
  if (!j.deadline) return '<span class="dday" style="color:var(--text-faint);font-weight:500">마감일 미상</span>';
  if (j.deadline === "상시") return '<span class="dday">상시모집</span>';
  const n = dayDiff(j.deadline);
  if (n === null) return "";
  if (n < 0) return '<span class="dday over">마감</span>';
  if (n === 0) return '<span class="dday soon">오늘 마감</span>';
  return '<span class="dday' + (n <= 7 ? " soon" : "") + '">D-' + n + "</span>";
}

function render() {
  let rows = jobs.filter(match);
  if (state.sort === "deadline") {
    rows = rows.slice().sort((a, b) => {
      const av = a.deadline && a.deadline !== "상시" ? a.deadline : "9999-99-99";
      const bv = b.deadline && b.deadline !== "상시" ? b.deadline : "9999-99-99";
      return av.localeCompare(bv);
    });
  } else {
    rows = rows.slice().sort((a, b) => (b.posted || "").localeCompare(a.posted || ""));
  }

  countEl.innerHTML = "<b>" + rows.length + "</b>건";
  const shown = rows.slice(0, state.limit);

  if (!shown.length) {
    listEl.innerHTML = '<li class="empty">조건에 맞는 공고가 없습니다. 필터를 넓혀 보세요.</li>';
    moreEl.innerHTML = "";
    return;
  }

  listEl.innerHTML = shown.map(j => {
    const cls = (BADGE[j.employment] || "b-etc");
    const kind = cls.replace("b-", "k-");
    const closed = isOpen(j) ? "" : " closed";
    const board = j.link_is_board
      ? ' <span class="board-note" title="이 기관은 공고별 주소를 제공하지 않아 게시판으로 연결됩니다">게시판</span>' : "";
    return '<li class="job ' + kind + closed + '">'
      + '<h3 class="t"><a href="' + esc(j.url) + '" target="_blank" rel="noopener">' + esc(j.title) + "</a></h3>"
      + '<div class="meta">'
      +   '<span class="badge ' + cls + '">' + esc(j.employment) + "</span>"
      +   '<span class="org">' + esc(j.org || "-") + "</span>"
      +   (j.location ? "<span>" + esc(j.location) + "</span>" : "")
      +   '<span class="src">' + esc(j.source) + "</span>" + board
      + "</div>"
      + '<div class="when">' + ddayHtml(j)
      +   "<span>" + (j.posted ? "등록 " + esc(j.posted.slice(5)) : "") + "</span>"
      + "</div>"
      + "</li>";
  }).join("");

  moreEl.innerHTML = rows.length > state.limit
    ? '<button class="tbtn" id="moreBtn">' + (rows.length - state.limit) + "건 더 보기</button>" : "";
  const mb = document.getElementById("moreBtn");
  if (mb) mb.onclick = () => { state.limit += 100; render(); };
}

document.querySelectorAll(".chip[data-emp]").forEach(b => {
  b.onclick = () => {
    const v = b.dataset.emp;
    if (state.emp.has(v)) state.emp.delete(v); else state.emp.add(v);
    b.setAttribute("aria-pressed", state.emp.has(v) ? "true" : "false");
    state.limit = 60; render();
  };
});
$("#q").oninput = e => { state.q = e.target.value.trim().toLowerCase(); state.limit = 60; render(); };
$("#org").onchange = e => { state.org = e.target.value; state.limit = 60; render(); };
$("#src").onchange = e => { state.src = e.target.value; state.limit = 60; render(); };
$("#sort").onchange = e => { state.sort = e.target.value; render(); };
$("#openOnly").onclick = e => {
  state.open = !state.open;
  e.currentTarget.setAttribute("aria-pressed", state.open ? "true" : "false");
  state.limit = 60; render();
};
$("#reset").onclick = () => {
  state.q = ""; state.emp.clear(); state.org = ""; state.src = ""; state.open = true; state.limit = 60;
  $("#q").value = ""; $("#org").value = ""; $("#src").value = "";
  $("#openOnly").setAttribute("aria-pressed", "true");
  document.querySelectorAll(".chip[data-emp]").forEach(b => b.setAttribute("aria-pressed", "false"));
  render();
};

render();
"""


def build():
    with open(DATA, encoding="utf-8") as f:
        d = json.load(f)
    jobs = d["jobs"]

    emp_counts = Counter(j["employment"] for j in jobs)
    org_types = sorted({j["org_type"] for j in jobs if j.get("org_type")})
    sources = Counter(j["source"] for j in jobs)
    src_kind = {}
    for j in jobs:
        src_kind.setdefault(j["source"], j.get("source_kind", ""))

    today = datetime.now().strftime("%Y-%m-%d")
    open_cnt = sum(1 for j in jobs
                   if not j.get("deadline") or j["deadline"] in ("상시",) or j["deadline"] >= today)
    target_cnt = sum(1 for j in jobs if j.get("is_target"))
    soon_cnt = sum(1 for j in jobs
                   if j.get("deadline") and j["deadline"] not in ("상시",)
                   and today <= j["deadline"] <= (datetime.now().replace(day=datetime.now().day)
                                                  .strftime("%Y-%m-%d")[:8] + "99"))
    # D-7 이내
    from datetime import timedelta
    week = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    soon_cnt = sum(1 for j in jobs
                   if j.get("deadline") and j["deadline"] not in ("상시",)
                   and today <= j["deadline"] <= week)

    # 고용형태 칩: 관심 순서대로
    chip_order = ["무기계약직", "공무직", "기간제·계약직", "교원·강사·연구직",
                  "공무원(임기제·개방형)", "정규직", "기타"]
    chips = "".join(
        f'<button class="chip" data-emp="{e}" aria-pressed="false">{e}'
        f'<span class="c">{emp_counts[e]}</span></button>'
        for e in chip_order if emp_counts.get(e))

    org_opts = "".join(f'<option value="{o}">{o}</option>' for o in org_types)
    src_opts = "".join(f'<option value="{s}">{s} ({n})</option>'
                       for s, n in sources.most_common())

    src_items = "".join(
        f'<div class="srcitem"><span class="nm">{s}<span class="kd">{src_kind.get(s, "")}</span></span>'
        f'<span class="ct">{n}</span></div>'
        for s, n in sources.most_common())

    payload = json.dumps({"jobs": jobs}, ensure_ascii=False, separators=(",", ":"))
    badge_js = json.dumps(BADGE, ensure_ascii=False)

    body = f"""<div class="wrap">
  <header class="masthead">
    <div>
      <p class="eyebrow">울산광역시 공공부문 채용</p>
      <h1>울산 공공기관 무기계약직·기간제 채용 모아보기</h1>
      <p class="sub">공공기관, 지자체, 교육청, 대학까지 {len(sources)}개 채용 사이트에 흩어져 있는
        울산 지역 공고를 한 곳에 모았습니다. 제목을 누르면 원문 공고로 이동합니다.</p>
    </div>
    <div class="updated">{d['generated_at']} 기준<br>최근 {d['days']}일 ({d['cutoff']} 이후)</div>
  </header>

  <section class="stats">
    <div class="stat"><span class="n">{len(jobs)}</span><span class="k">전체 공고</span></div>
    <div class="stat hi"><span class="n">{target_cnt}</span><span class="k">무기계약·공무직·기간제</span></div>
    <div class="stat"><span class="n">{open_cnt}</span><span class="k">접수 진행중</span></div>
    <div class="stat warn"><span class="n">{soon_cnt}</span><span class="k">7일 내 마감</span></div>
  </section>

  <section class="filters" aria-label="검색 및 필터">
    <div class="frow">
      <input type="search" id="q" placeholder="제목, 기관명, 지역으로 검색 (예: 조리실무사, 울주군, 기간제)"
             aria-label="공고 검색">
      <select id="org" aria-label="기관 유형"><option value="">기관 유형 전체</option>{org_opts}</select>
      <select id="src" aria-label="출처"><option value="">출처 전체</option>{src_opts}</select>
      <select id="sort" aria-label="정렬">
        <option value="posted">최신 등록순</option>
        <option value="deadline">마감 임박순</option>
      </select>
    </div>
    <div class="frow">
      {chips}
      <button class="chip" id="openOnly" aria-pressed="true">접수중만</button>
      <button class="chip" id="reset">초기화</button>
      <span class="count" id="count"></span>
    </div>
  </section>

  <ul class="list" id="list"></ul>
  <div class="more" id="more"></div>

  <section class="sources">
    <h2>수집 대상 사이트</h2>
    <p>아래 {len(sources)}곳을 자동으로 훑어 울산 지역 공고만 골라냈습니다.
      합격자 발표나 심사 결과 같은 안내글은 지원할 자리가 아니라서 뺐습니다.
      <b>게시판</b> 표시가 붙은 공고는 해당 기관이 공고별 고유 주소를 제공하지 않아
      채용공고 목록 페이지로 연결됩니다.</p>
    <div class="srcgrid">{src_items}</div>
    <p class="foot">
      고용형태는 공고 제목과 기관이 제공한 채용구분을 기준으로 자동 분류한 것이라
      실제 계약 조건과 다를 수 있습니다. 지원 전 반드시 원문 공고를 확인하세요.<br>
      데이터 갱신: <code>python collect.py --days 60</code> 실행 후 <code>python build_site.py</code>
    </p>
  </section>
</div>

<script>
window.__JOBS__ = {payload};
const BADGE = {badge_js};
</script>
<script>{JS}</script>"""

    os.makedirs(SITE, exist_ok=True)

    # 아티팩트 발행용(본문만)
    art = f"<title>울산 공공기관 채용 모아보기</title>\n<style>{CSS}</style>\n{body}"
    with open(os.path.join(SITE, "artifact.html"), "w", encoding="utf-8") as f:
        f.write(art)

    # 로컬에서 바로 여는 완성본
    full = ("<!doctype html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>울산 공공기관 채용 모아보기</title>\n"
            f"<style>{CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>")
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(full)

    print(f"생성 완료  ({len(jobs)}건)")
    print("  ", os.path.join(SITE, "index.html"))
    print("  ", os.path.join(SITE, "artifact.html"))


if __name__ == "__main__":
    build()
