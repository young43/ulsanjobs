# -*- coding: utf-8 -*-
"""
울산 공공기관 채용공고 수집기
------------------------------------------------------------------
울산 지역의 공공기관/지자체/교육청/대학 채용공고를 여러 사이트에서 모아
data/jobs.json 으로 저장한다.

사용법:
    python collect.py              # 최근 60일
    python collect.py --days 30    # 기간 지정
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import ssl
import sys
import time
import traceback
from datetime import datetime, timedelta
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

requests.packages.urllib3.disable_warnings()

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


class LegacyTLSAdapter(HTTPAdapter):
    """일부 .go.kr 서버가 구형 TLS 재협상을 요구해서 필요하다."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.options |= 0x4  # SSL_OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        except Exception:
            pass
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(UA)
    s.verify = False
    s.mount("https://", LegacyTLSAdapter())
    return s


S = make_session()

# ------------------------------------------------------------------
# 공통 유틸
# ------------------------------------------------------------------

DATE_RE = re.compile(r"(20\d{2})[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})")
SHORT_DATE_RE = re.compile(r"\b(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")


def parse_date(text: str) -> str:
    """문자열에서 첫 번째 날짜를 YYYY-MM-DD 로 뽑는다. 없으면 ''."""
    if not text:
        return ""
    m = DATE_RE.search(text)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = SHORT_DATE_RE.search(text)
    if m:
        y, mo, d = m.groups()
        return f"20{int(y):02d}-{int(mo):02d}-{int(d):02d}"
    return ""


def parse_deadline(text: str) -> str:
    """마감일: 문자열에 날짜가 2개면 뒤쪽(마감), 1개면 그것."""
    if not text:
        return ""
    found = [f"{int(y):04d}-{int(mo):02d}-{int(d):02d}" for y, mo, d in DATE_RE.findall(text)]
    if not found:
        found = [f"20{int(y):02d}-{int(mo):02d}-{int(d):02d}" for y, mo, d in SHORT_DATE_RE.findall(text)]
    if not found:
        if any(k in text for k in ("채용시까지", "상시", "수시")):
            return "상시"
        return ""
    return found[-1]


# 고용형태 분류 -- 앞쪽 규칙이 우선한다
EMPLOYMENT_RULES = [
    ("무기계약직", ["무기계약"]),
    ("공무직", ["공무직", "교육공무직"]),
    ("기간제·계약직", ["기간제", "한시", "대체인력", "시간선택제", "시간제", "일용",
                    "계약직", "단기", "비정규직", "체험형", "청년인턴", "인턴"]),
    ("공무원(임기제·개방형)", ["임기제", "개방형", "공모직위", "군무원", "공무원"]),
    ("교원·강사·연구직", ["교수", "초빙", "강사", "교강사", "조교", "연구원",
                     "post-doc", "postdoc", "박사후", "전임교원", "비전임", "교원", "교사"]),
    ("정규직", ["정규직", "신입", "공개경쟁", "정직원", "교직원", "직원", "사무원"]),
]

# 채용 결과·현황 안내글. 지원할 수 있는 자리가 아니라서 목록에서 뺀다.
RESULT_NOTICE = re.compile(
    # '임용시험 시행계획 공고'는 실제 채용공고라서 넣지 말 것
    r"(합격자|합격 ?발표|서류심사 결과|서류전형 결과|심사 결과|결과 ?발표|최종 ?합격"
    r"|채용현황 공개|친인척|응시번호|원서접수 현황|면접시험 (시간|장소))")

TARGET_TYPES = {"무기계약직", "공무직", "기간제·계약직"}


def classify_employment(*parts: str) -> str:
    text = " ".join(p for p in parts if p).lower()
    for label, keys in EMPLOYMENT_RULES:
        for k in keys:
            if k.lower() in text:
                return label
    return "기타"


ULSAN_HINTS = ["울산", "울주", "unist", "울산과학기술원", "춘해"]


def mentions_ulsan(*parts: str) -> bool:
    text = " ".join(p for p in parts if p).lower()
    return any(h in text for h in ULSAN_HINTS)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def pick_table(soup: BeautifulSoup, must_have: list[str]):
    """헤더에 must_have 가 모두 들어간 표 중 행이 가장 많은 것을 고른다.

    레이아웃용 표나 검색폼 표를 잘못 집는 것을 막는다.
    """
    best, best_n = None, 0
    for t in soup.find_all("table"):
        heads = " ".join(clean(th.get_text()) for th in t.find_all("th"))
        if not all(k in heads for k in must_have):
            continue
        n = len(t.find_all("tr"))
        if n > best_n:
            best, best_n = t, n
    return best


# 고시공고 게시판처럼 채용 외 공고가 섞인 곳에서 채용글만 남기기 위한 판정
JOB_WORDS = ("채용", "근로자", "기간제", "무기계약", "공무직", "임용", "구인", "초빙", "선발")
NOT_JOB_WORDS = ("위원 모집", "위원회", "위원 공모", "공모전", "교육생", "수강생", "참가자",
                 "입찰", "낙찰", "매각", "임대", "분양", "지원사업", "공모사업",
                 "동아리", "체험단", "봉사자", "명예", "자원봉사")


def looks_like_job(title: str) -> bool:
    if any(b in title for b in NOT_JOB_WORDS):
        # '채용'/'근로자'가 함께 있으면 채용글로 본다
        return any(g in title for g in ("채용", "근로자", "기간제", "무기계약", "공무직"))
    return any(g in title for g in JOB_WORDS) or "모집" in title


def older_than(dates: list[str], cutoff: str) -> bool:
    """수집한 날짜가 모두 기준일보다 과거면 True (페이지 조기 종료용)."""
    real = [d for d in dates if d]
    return bool(real) and all(d < cutoff for d in real)


def get(url, **kw):
    kw.setdefault("timeout", 30)
    return S.get(url, **kw)


def post(url, **kw):
    kw.setdefault("timeout", 30)
    return S.post(url, **kw)


class Collector:
    """소스별 수집 결과와 에러를 모은다."""

    def __init__(self, cutoff: str):
        self.cutoff = cutoff
        self.jobs: list[dict] = []
        self.report: list[dict] = []

    def add(self, source: str, rows: list[dict], note: str = "", error: str = ""):
        kept = 0
        dropped = 0
        for r in rows:
            posted = r.get("posted") or ""
            # 등록일이 있으면 기간 필터, 없으면 통과
            if posted and posted < self.cutoff:
                continue
            # 합격자 발표 같은 결과 안내글은 제외
            if RESULT_NOTICE.search(r.get("title", "")):
                dropped += 1
                continue
            r.setdefault("employment", classify_employment(r.get("title", ""), r.get("category", "")))
            r["is_target"] = r["employment"] in TARGET_TYPES
            self.jobs.append(r)
            kept += 1
        self.report.append({"source": source, "fetched": len(rows), "kept": kept,
                            "dropped_result_notice": dropped, "note": note, "error": error})
        status = "ERR " if error else "OK  "
        drop = f" (결과글 {dropped} 제외)" if dropped else ""
        print(f"  {status}{source:<22} 수집 {len(rows):>4} → 남김 {kept:>4}{drop}  {note}{error}")

    def run(self, source: str, fn, note: str = ""):
        try:
            rows = fn()
            self.add(source, rows, note=note)
        except Exception as e:
            self.add(source, [], error=f"{type(e).__name__}: {str(e)[:120]}")
            if os.environ.get("ULSANJOBS_DEBUG"):
                traceback.print_exc()


# ==================================================================
# 1. 잡알리오 -- 중앙 공공기관 (울산 근무지)
# ==================================================================

def fetch_alio(days: int) -> list[dict]:
    base = "https://job.alio.go.kr"
    s_date = (datetime.now() - timedelta(days=days)).strftime("%Y.%m.%d")
    e_date = datetime.now().strftime("%Y.%m.%d")
    out, seen = [], set()

    for page in range(1, 8):
        params = [
            ("pageNo", str(page)), ("search_yn", "Y"), ("order", "REG_DATE"),
            ("sort", "DESC"), ("pageSet", "50"),
            ("location", "R3016"),          # 울산광역시
            ("s_date", s_date), ("e_date", e_date),
        ]
        r = get(f"{base}/recruit.do", params=params)
        soup = BeautifulSoup(r.text, "html.parser")
        # '공공기관 채용정보 검색'(검색폼) 표가 아니라 결과 표를 골라야 한다
        table = pick_table(soup, ["채용제목", "기관명"])
        if table is None:
            break
        rows = table.find_all("tr")[1:]
        if not rows:
            break
        added = 0
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 8:
                continue
            a = tr.find("a", href=True)
            if not a:
                continue
            idx = re.search(r"idx=(\d+)", a["href"])
            if not idx:
                continue
            key = idx.group(1)
            if key in seen:
                continue
            seen.add(key)
            # 제목 텍스트는 <a> 바깥에 있어서 셀 전체를 읽어야 한다
            title = clean(tds[2].get_text())
            org = clean(tds[3].get_text())
            work = clean(tds[4].get_text())
            etype = clean(tds[5].get_text())
            posted = parse_date(clean(tds[6].get_text()))
            deadline = parse_deadline(clean(tds[7].get_text()))
            status = clean(tds[8].get_text()) if len(tds) > 8 else ""
            out.append({
                "id": f"alio:{key}",
                "source": "잡알리오",
                "source_kind": "중앙 공공기관",
                "url": f"{base}/recruitview.do?idx={key}",
                "title": title,
                "org": org,
                "org_type": "공공기관",
                "location": work,
                "category": etype,
                "posted": posted,
                "deadline": deadline,
                "status": status,
                "employment": classify_employment(etype, title),
            })
            added += 1
        if added == 0:
            break
        time.sleep(0.3)
    return out


# ==================================================================
# 2. 나라일터 -- 국가기관/지자체/교육청/공공기관 (울산)
# ==================================================================

def fetch_gojobs(days: int) -> list[dict]:
    """나라일터.

    지역 select(serachAreaClassCd)는 GET/POST 어느 쪽으로 넣어도 서버가 무시한다.
    대신 키워드 검색이 제대로 동작해서 '울산'/'울주'로 조회한다.
    """
    out, seen = [], set()
    for kw in ("울산", "울주"):
        out.extend(_gojobs_keyword(kw, days, seen))
    return out


def _gojobs_keyword(keyword: str, days: int, seen: set) -> list[dict]:
    base = "https://gojobs.go.kr"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for page in range(1, 12):
        params = {
            "menuNo": "401", "mngrMenuYn": "N", "selMenuNo": "400",
            "searchKeyword": keyword,
            "pageIndex": str(page),
        }
        r = get(f"{base}/apmList.do", params=params)
        soup = BeautifulSoup(r.text, "html.parser")
        table = None
        for t in soup.find_all("table"):
            head = [th.get_text(strip=True) for th in t.find_all("th")]
            if "공고명" in head and "기관명" in head:
                table = t
                break
        if table is None:
            break
        rows = table.find_all("tr")[1:]
        if not rows:
            break
        added = 0
        page_dates = []
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            a = tr.find("a", href=True) or tr.find("a")
            title = clean(tds[1].get_text()) or clean(a.get_text() if a else "")
            if not title:
                continue
            href = (a.get("href") if a else "") or ""
            # href 는 javascript:fn_apmView('020', '300595') 형태다
            m = re.search(r"fn_apmView\(\s*'([^']*)'\s*,\s*'(\d+)'\s*\)", href)
            key = m.group(2) if m else ""
            url = (f"{base}/apmView.do?empmnsn={key}&menuNo=401" if key
                   else f"{base}/apmList.do?menuNo=401&searchKeyword={quote(keyword)}")
            org = clean(tds[2].get_text())
            posted = parse_date(clean(tds[3].get_text()))
            deadline = parse_deadline(clean(tds[4].get_text()))
            page_dates.append(posted)
            # 키워드 검색이라 무관한 지역이 섞이지 않는지 한 번 더 확인한다
            if not mentions_ulsan(title, org):
                continue
            uid = f"gojobs:{key or title[:30]}"
            if uid in seen:
                continue
            seen.add(uid)
            out.append({
                "id": uid,
                "source": "나라일터",
                "source_kind": "국가·지자체·공공기관",
                "url": url,
                "title": title,
                "org": org,
                "org_type": "공공기관·지자체",
                "location": "울산",
                "category": "",
                "posted": posted,
                "deadline": deadline,
                "status": "",
                "employment": classify_employment(title),
            })
            added += 1
        if older_than(page_dates, cutoff):
            break
        time.sleep(0.3)
    return out


# ==================================================================
# 3. 클린아이 잡플러스 -- 지방공기업/출자출연기관
# ==================================================================

def fetch_cleaneye(days: int, max_pages: int = 400) -> list[dict]:
    base = "https://job.cleaneye.go.kr"
    ajax = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{base}/user/ypRecruitment.do"}
    get(f"{base}/user/ypRecruitment.do")
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    stale_pages = 0
    for page in range(1, max_pages + 1):
        r = post(f"{base}/user/selectYpRecruitment.do", data={"pageIndex": str(page)}, headers=ajax)
        try:
            payload = r.json()
        except Exception:
            break
        items = payload.get("list") or []
        if not items:
            break
        page_dates = []
        for rec in items:
            pub = (rec.get("pubDate") or "")[:10]
            page_dates.append(pub)
            ent = rec.get("entName") or ""
            local = rec.get("localName") or ""
            title = rec.get("entTitle") or ""
            if rec.get("sidoCd") != "007007" and not mentions_ulsan(ent, local):
                continue
            out.append({
                "id": f"cleaneye:{rec.get('ypEntId')}-{rec.get('entSeq')}-{rec.get('empyear')}",
                "source": "클린아이 잡플러스",
                "source_kind": "지방공기업·출자출연기관",
                # 클린아이는 공고별 고유 주소가 없어 검색 결과로 연결한다
                "url": f"{base}/search.do?kwd={quote(title[:40])}",
                "link_is_board": True,
                "title": clean(title),
                "org": clean(ent),
                "org_type": "지방공공기관",
                "location": clean(local) or "울산",
                "category": "",
                "posted": pub,
                "deadline": (rec.get("pubEndDate") or "")[:10],
                "status": "",
                "employment": classify_employment(title),
            })
        # 최신순 정렬이므로 페이지 전체가 기준일보다 오래되면 중단
        if page_dates and all(d and d < cutoff for d in page_dates):
            stale_pages += 1
            if stale_pages >= 2:
                break
        else:
            stale_pages = 0
        time.sleep(0.15)
    return out


# ==================================================================
# 4. 하이브레인넷 -- 대학·연구기관 (울산 A16)
# ==================================================================

def fetch_hibrain(days: int) -> list[dict]:
    base = "https://www.hibrain.net"
    out, seen = [], set()
    for page in range(1, 6):
        url = f"{base}/recruitment/categories/ARAGP/categories/A16/recruits"
        r = get(url, params={"page": str(page), "limit": "25", "listType": "ING",
                             "sortType": "SORTDTM", "displayType": "TIT"})
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("li.row.sortRoot")
        if not rows:
            break
        added = 0
        for li in rows:
            a = li.select_one("span.td_title a") or li.find("a", href=True)
            if not a:
                continue
            href = a.get("href") or ""
            m = re.search(r"/recruits/(\d+)", href)
            if not m:
                continue
            key = m.group(1)
            if key in seen:
                continue
            seen.add(key)
            title_el = li.select_one("span.title")
            title = clean(title_el.get_text() if title_el else a.get_text())
            spans = [clean(s.get_text()) for s in li.find_all("span", recursive=False)]
            tail = " ".join(spans[-2:]) if spans else ""
            posted, deadline = "", ""
            dates = DATE_RE.findall(tail) or SHORT_DATE_RE.findall(tail)
            norm = []
            for y, mo, d in dates:
                yy = int(y) if len(y) == 4 else 2000 + int(y)
                norm.append(f"{yy:04d}-{int(mo):02d}-{int(d):02d}")
            if len(norm) >= 3:
                deadline, posted = norm[1], norm[2]
            elif len(norm) == 2:
                deadline, posted = norm[0], norm[1]
            elif len(norm) == 1:
                posted = norm[0]
            if "채용시까지" in tail and not deadline:
                deadline = "상시"
            org = title.split()[0] if title else ""
            out.append({
                "id": f"hibrain:{key}",
                "source": "하이브레인넷",
                "source_kind": "대학·연구기관",
                "url": urljoin(base, href.split("?")[0]),
                "title": title,
                "org": org,
                "org_type": "대학·연구기관",
                "location": "울산",
                "category": "",
                "posted": posted,
                "deadline": deadline,
                "status": "",
                "employment": classify_employment(title),
            })
            added += 1
        if added == 0:
            break
        time.sleep(0.3)
    return out


# ==================================================================
# 5. 울산광역시 고시공고
# ==================================================================

def fetch_ulsan_city(days: int) -> list[dict]:
    base = "https://www.ulsan.go.kr"
    list_url = f"{base}/u/rep/transfer/notice/list.ulsan"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for page in range(1, 61):
        r = get(list_url, params={"mId": "001004002000000000", "curPage": str(page)})
        soup = BeautifulSoup(r.text, "html.parser")
        table = pick_table(soup, ["제목"]) or soup.find("table")
        if table is None:
            break
        rows = table.find_all("tr")[1:]
        if not rows:
            break
        page_dates = []
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
            page_dates.append(parse_date(clean(tds[5].get_text())))
            a = tr.find("a", href=True)
            if not a:
                continue
            title = clean(a.get_text())
            # 고시공고 게시판이라 채용 외 공고가 대부분이다
            if not looks_like_job(title):
                continue
            href = a["href"]
            url = urljoin(f"{base}/u/rep/transfer/notice/", href.lstrip("./"))
            out.append({
                "id": f"ulsan:{clean(tds[4].get_text()) or title[:24]}",
                "source": "울산광역시 고시공고",
                "source_kind": "광역자치단체",
                "url": url,
                "title": title,
                "org": "울산광역시 " + clean(tds[2].get_text()),
                "org_type": "지자체",
                "location": "울산",
                "category": "",
                "posted": parse_date(clean(tds[5].get_text())),
                "deadline": "",
                "status": "",
                "employment": classify_employment(title),
            })
        if older_than(page_dates, cutoff):
            break
        time.sleep(0.3)
    return out


# ==================================================================
# 6. 울산 중구 채용공고
# ==================================================================

def fetch_junggu(days: int) -> list[dict]:
    base = "https://www.junggu.ulsan.kr"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for page in range(1, 16):
        r = get(f"{base}/board/list.ulsan", params={
            "boardId": "BBS_0000060", "menuCd": "DOM_000000102004002000",
            "paging": "ok", "listRow": "10", "listCel": "1", "startPage": str(page)})
        soup = BeautifulSoup(r.text, "html.parser")
        table = pick_table(soup, ["제목"]) or soup.find("table")
        if table is None:
            break
        rows = table.find_all("tr")[1:]
        if not rows:
            break
        page_dates = []
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            page_dates.append(parse_date(clean(tds[4].get_text())))
            a = tr.find("a", href=True)
            if not a:
                continue
            title = clean(a.get_text())
            url = urljoin(base, a["href"])
            key = re.search(r"dataSid=(\d+)", a["href"])
            out.append({
                "id": f"junggu:{key.group(1) if key else title[:24]}",
                "source": "울산 중구 채용공고",
                "source_kind": "기초자치단체",
                "url": url,
                "title": title,
                "org": "울산 중구 " + clean(tds[2].get_text()),
                "org_type": "지자체",
                "location": "울산 중구",
                "category": "",
                "posted": parse_date(clean(tds[4].get_text())),
                "deadline": "",
                "status": "",
                "employment": classify_employment(title),
            })
        if older_than(page_dates, cutoff):
            break
        time.sleep(0.3)
    return out


# ==================================================================
# 7. 남구/동구/북구 -- 새올(eminwon) 채용공고
# ==================================================================

# (표기명, eminwon 호스트, 지역, 구청 홈페이지의 채용공고 게시판 주소)
# 새올(eminwon) 상세는 POST 폼 전송으로만 열려서 개별 공고 직링크를 만들 수 없다.
# 그래서 링크는 각 구청의 채용공고 게시판으로 연결한다.
EMINWON_SITES = [
    ("울산 남구", "eminwon.ulsannamgu.go.kr", "울산 남구",
     "https://www.ulsannamgu.go.kr/cop/bbs/selectSaeolHireList.do"),
    ("울산 동구", "eminwon.donggu.ulsan.kr", "울산 동구",
     "https://www.donggu.ulsan.kr/donggu/contents/contents.do?mId=4040201"),
    ("울산 북구", "eminwon.bukgu.ulsan.kr", "울산 북구",
     "https://www.bukgu.ulsan.kr/lay1/S1T92C460/contents.do"),
]


def fetch_eminwon(label: str, host: str, region: str, board_url: str, days: int) -> list[dict]:
    """새올 서버가 간헐적으로 빈 응답을 주기 때문에 몇 번 다시 시도한다."""
    for attempt in range(3):
        rows = _eminwon_once(label, host, region, board_url)
        if rows:
            return rows
        time.sleep(1.5 * (attempt + 1))
    return []


def _eminwon_once(label: str, host: str, region: str, board_url: str) -> list[dict]:
    ref = f"https://{host}/emwp/jsp/ofr/OfrNotAncmtLSub.jsp?not_ancmt_se_code=05&list_gubun="
    r = get(ref)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form", attrs={"name": "form1"}) or soup.find("form")
    if not form:
        return []
    data = {}
    for inp in form.find_all(["input", "select", "textarea"]):
        nm = inp.get("name")
        if nm:
            data[nm] = inp.get("value") or ""
    data.update({
        "method": "selectListOfrNotAncmt",
        "methodnm": "selectListOfrNotAncmtHomepage",
        "not_ancmt_se_code": "05",
        "pageIndex": "1",
    })
    p = post(f"https://{host}/emwp/gov/mogaha/ntis/web/ofr/action/OfrAction.do",
             data=data, headers={"Referer": ref})
    p.encoding = "utf-8"
    ps = BeautifulSoup(p.text, "html.parser")

    # 헤더에 '제목'이 있는 표 중 행이 가장 많은 것
    best, best_n = None, 0
    for t in ps.find_all("table"):
        heads = [clean(th.get_text()) for th in t.find_all("th")]
        if "제목" not in " ".join(heads):
            continue
        n = len(t.find_all("tr"))
        if n > best_n:
            best, best_n = t, n
    if best is None:
        return []

    heads = [clean(th.get_text()) for th in best.find_all("th")]
    try:
        ti = heads.index("제목")
    except ValueError:
        ti = 1
    di = next((i for i, h in enumerate(heads) if "등록" in h), None)

    out = []
    for tr in best.find_all("tr"):
        tds = tr.find_all("td")
        # 셀이 지나치게 많은 행은 표 전체를 감싼 래퍼 행이라 건너뛴다(북구)
        if len(tds) < max(ti + 1, 3) or len(tds) > 10:
            continue
        a = tr.find("a")
        if not a:
            continue
        # 북구는 <a> 가 번호 칸에 붙어 있어 제목 칸을 직접 읽어야 한다
        title = clean(tds[ti].get_text()) if ti < len(tds) else clean(a.get_text())
        if not title or title in ("제목",) or title.isdigit():
            continue
        onclick = str(a.get("onclick") or a.get("href") or "")
        m = re.search(r"searchDetail\('?(\d+)'?\)", onclick)
        key = m.group(1) if m else title[:24]
        posted = parse_date(clean(tds[di].get_text())) if (di is not None and di < len(tds)) else ""
        if not posted:
            posted = parse_date(clean(tr.get_text()))
        dept = ""
        for i, h in enumerate(heads):
            if ("부서" in h or "작성자" in h) and i < len(tds):
                dept = clean(tds[i].get_text())
                break
        out.append({
            "id": f"eminwon:{host}:{key}",
            "source": f"{label} 채용공고",
            "source_kind": "기초자치단체",
            "url": board_url,
            "link_is_board": True,
            "title": title,
            "org": f"{label} {dept}".strip(),
            "org_type": "지자체",
            "location": region,
            "category": "",
            "posted": posted,
            "deadline": "",
            "status": "",
            "employment": classify_employment(title),
        })
    return out


# ==================================================================
# 8. 울주군 채용공고
# ==================================================================

def fetch_ulju(days: int) -> list[dict]:
    base = "https://www.ulju.ulsan.kr"
    r = get(f"{base}/ulju/unit/employ/list.do", params={"mId": "0403030100"})
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    ids = re.findall(r"notAncmtMgtNo['\"]?\s*[:=,]\s*['\"]?(\d+)", r.text)
    out = []
    rows = table.find_all("tr")[1:]
    for i, tr in enumerate(rows):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        a = tr.find("a")
        title = clean(a.get_text() if a else tds[2].get_text())
        if not title:
            continue
        # 행 안의 hidden input 에서 관리번호를 우선 찾고, 없으면 페이지 순서대로 대응
        key = ""
        hid = tr.find("input", attrs={"name": re.compile("notAncmtMgtNo", re.I)})
        if hid and hid.get("value"):
            key = hid["value"]
        elif i < len(ids):
            key = ids[i]
        url = (f"{base}/ulju/unit/employ/view.do?notAncmtMgtNo={key}&mId=0403030100"
               if key else f"{base}/ulju/unit/employ/list.do?mId=0403030100")
        out.append({
            "id": f"ulju:{key or title[:24]}",
            "source": "울주군 채용공고",
            "source_kind": "기초자치단체",
            "url": url,
            "title": title,
            "org": "울주군 " + clean(tds[3].get_text()),
            "org_type": "지자체",
            "location": "울산 울주군",
            "category": clean(tds[1].get_text()),
            "posted": parse_date(clean(tds[4].get_text())),
            "deadline": "",
            "status": "",
            "employment": classify_employment(title),
        })
    return out


# ==================================================================
# 9. 울산광역시교육청 -- 교육공무직 / 일반채용 / 온라인채용
# ==================================================================

def _use_bbs(bbs_sn: str, label: str, prefix: str, days: int, host_path: str) -> list[dict]:
    base = "https://use.go.kr"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for page in range(1, 41):
        # q_currPage 만 보내면 목록 표가 통째로 빠진 페이지가 돌아온다. q_rowPerPage 를 같이 보낼 것
        r = get(f"{base}{host_path}/user/bbs/BD_selectBbsList.do",
                params={"q_bbsSn": bbs_sn, "q_currPage": str(page), "q_rowPerPage": "10"})
        soup = BeautifulSoup(r.text, "html.parser")
        table = pick_table(soup, ["제목"]) or pick_table(soup, ["모집명"])
        if table is None:
            break
        heads = [clean(th.get_text()) for th in table.find_all("th")]
        rows = table.find_all("tr")[1:]
        if not rows:
            break
        page_dates = []
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            a = tr.find("a", href=True)
            if not a:
                continue
            title = clean(a.get_text())
            doc = re.search(r"q_bbsDocNo=(\w+)", a["href"])
            key = doc.group(1) if doc else title[:24]
            url = f"{base}{host_path}/user/bbs/BD_selectBbs.do?q_bbsSn={bbs_sn}&q_bbsDocNo={key}"
            cells = [clean(td.get_text()) for td in tds]
            # 제목에 '2026.9.1.자' 같은 날짜가 섞여 있어서, 열을 지정해 읽어야 한다
            date_idx = next((i for i, h in enumerate(heads) if "작성일" in h or "등록일" in h), None)
            period_idx = next((i for i, h in enumerate(heads) if "접수" in h or "마감" in h), None)
            posted, deadline = "", ""
            if date_idx is not None and date_idx < len(cells):
                posted = parse_date(cells[date_idx])
            if period_idx is not None and period_idx < len(cells):
                deadline = parse_deadline(cells[period_idx])
            if not posted:
                # 등록일 칸이 없는 게시판은 문서번호(20260810155119066)에서 등록일을 얻는다
                m = re.match(r"(20\d{2})(\d{2})(\d{2})\d{6,}", key)
                if m:
                    posted = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                elif period_idx is not None and period_idx < len(cells):
                    posted = parse_date(cells[period_idx])
            category = ""
            for i, h in enumerate(heads):
                if h in ("구분", "분류", "직종") and i < len(cells):
                    category = cells[i]
                    break
            school = ""
            for i, h in enumerate(heads):
                if "학교" in h and i < len(cells):
                    school = cells[i]
                    break
            out.append({
                "id": f"{prefix}:{key}",
                "source": label,
                "source_kind": "교육청·학교",
                "url": url,
                "title": title,
                "org": school or "울산광역시교육청",
                "org_type": "교육청·학교",
                "location": "울산",
                "category": category,
                "posted": posted,
                "deadline": deadline,
                "status": "",
                "employment": classify_employment(category, title),
            })
            page_dates.append(posted)
        if older_than(page_dates, cutoff):
            break
        time.sleep(0.3)
    return out


def fetch_use_gongmujik(days: int) -> list[dict]:
    return _use_bbs("1918", "울산교육청 교육공무직", "use1918", days, "")


def fetch_use_general(days: int) -> list[dict]:
    return _use_bbs("2249", "울산교육청 일반채용", "use2249", days, "/job")


def fetch_use_online(days: int) -> list[dict]:
    """온라인채용공고: 학교별 기간제교사 등."""
    base = "https://use.go.kr"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for page in range(1, 41):
        r = get(f"{base}/job/user/jobpost/BD_selectJobPostList.do",
                params={"q_currPage": str(page), "q_rowPerPage": "10"})
        soup = BeautifulSoup(r.text, "html.parser")
        table = pick_table(soup, ["학교명"]) or soup.find("table")
        if table is None:
            break
        rows = table.find_all("tr")[1:]
        if not rows:
            break
        page_dates = []
        for tr in rows:
            tds = [clean(td.get_text()) for td in tr.find_all("td")]
            if len(tds) < 7:
                continue
            a = tr.find("a", href=True)
            no, school, region, jobkind, title, posted, deadline = tds[:7]
            key = ""
            if a:
                m = re.search(r"q_jobPostSn=(\d+)", (a.get("href") or "") + (a.get("onclick") or ""))
                if m:
                    key = m.group(1)
            url = (f"{base}/job/user/jobpost/BD_selectJobPost.do?q_jobPostSn={key}"
                   if key else f"{base}/job/user/jobpost/BD_selectJobPostList.do")
            out.append({
                "id": f"useonline:{key or no}",
                "source": "울산교육청 온라인채용",
                "source_kind": "교육청·학교",
                "url": url,
                "title": title or clean(a.get_text() if a else ""),
                "org": school,
                "org_type": "교육청·학교",
                "location": f"울산 {region}".strip(),
                "category": jobkind,
                "posted": parse_date(posted),
                "deadline": parse_deadline(deadline),
                "status": "",
                "employment": classify_employment(jobkind, title),
            })
            page_dates.append(parse_date(posted))
        if older_than(page_dates, cutoff):
            break
        time.sleep(0.3)
    return out


# ==================================================================
# 10. 울산 소재 대학 자체 채용게시판
#     하이브레인넷은 교수·연구원 공고 위주라 대학 행정직원/조교 공고가 빠진다.
# ==================================================================

def _deadline_from_title(title: str, posted: str) -> str:
    """제목의 '(~8/18)' 같은 표기에서 마감일을 뽑는다."""
    m = re.search(r"~\s*(\d{1,2})\s*[/.]\s*(\d{1,2})", title)
    if not m or not posted:
        return ""
    mo, d = int(m.group(1)), int(m.group(2))
    year = int(posted[:4])
    # 12월 등록 → 1월 마감처럼 해가 넘어가는 경우
    if mo < int(posted[5:7]):
        year += 1
    try:
        return f"{year:04d}-{mo:02d}-{d:02d}"
    except Exception:
        return ""


def fetch_chunhae(days: int) -> list[dict]:
    """춘해보건대학교 공지사항 중 채용글."""
    base = "https://www.ch.ac.kr"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for page in range(1, 9):
        r = get(f"{base}/board/board.php",
                params={"site_id": "main", "TREE_NO": "16087", "DEPTH": "2", "pagecnt": str(page)})
        soup = BeautifulSoup(r.text, "html.parser")
        table = pick_table(soup, ["제목", "등록일"])
        if table is None:
            break
        rows = table.find_all("tr")[1:]
        if not rows:
            break
        page_dates = []
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            a = tr.find("a", href=True)
            if not a:
                continue
            # 제목 칸에 등록일·조회수가 같이 들어 있어서 첫 span 만 읽는다
            span = a.find("span")
            title = clean(span.get_text()) if span else clean(tds[1].get_text())
            title = re.sub(r"^공지\s*", "", title)
            posted = parse_date(clean(tds[3].get_text()))
            page_dates.append(posted)
            if not looks_like_job(title):
                continue
            key = re.search(r"letter_no=(\d+)", a["href"])
            out.append({
                "id": f"chunhae:{key.group(1) if key else title[:24]}",
                "source": "춘해보건대학교",
                "source_kind": "대학 자체 채용",
                "url": urljoin(f"{base}/board/", a["href"]),
                "title": title,
                "org": "춘해보건대학교",
                "org_type": "대학·연구기관",
                "location": "울산",
                "category": "",
                "posted": posted,
                "deadline": _deadline_from_title(title, posted),
                "status": "",
                "employment": classify_employment(title),
            })
        if older_than(page_dates, cutoff):
            break
        time.sleep(0.3)
    return out


def fetch_ulsan_college(days: int) -> list[dict]:
    """울산과학대학교 채용공고 게시판."""
    base = "https://www.uc.ac.kr/www/CMS/Board/Board.do"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for page in range(1, 9):
        r = get(base, params={"mCode": "MN099", "page": str(page)})
        soup = BeautifulSoup(r.text, "html.parser")
        table = pick_table(soup, ["제목", "접수기간"])
        if table is None:
            break
        rows = table.find_all("tr")[1:]
        if not rows:
            break
        page_dates = []
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            a = tr.find("a", href=True)
            if not a:
                continue
            title = clean(tds[1].get_text())
            if not title:
                continue
            period = clean(tds[2].get_text())
            posted = parse_date(period)
            deadline = parse_deadline(period)
            page_dates.append(posted)
            key = re.search(r"board_seq=(\d+)", a["href"])
            out.append({
                "id": f"uc:{key.group(1) if key else title[:24]}",
                "source": "울산과학대학교",
                "source_kind": "대학 자체 채용",
                "url": urljoin(base, a["href"]),
                "title": title,
                "org": "울산과학대학교",
                "org_type": "대학·연구기관",
                "location": "울산",
                "category": clean(tds[3].get_text()),
                "posted": posted,
                "deadline": deadline,
                "status": clean(tds[3].get_text()),
                "employment": classify_employment(title),
            })
        if older_than(page_dates, cutoff):
            break
        time.sleep(0.3)
    return out


def fetch_ulsan_univ(days: int) -> list[dict]:
    """울산대학교 교직원채용시스템.

    공고별 주소가 javascript 함수라서 링크는 채용시스템 첫 화면으로 연결한다.
    """
    home = "https://recruit.ulsan.ac.kr/"
    r = get(home, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen = [], set()
    for a in soup.select("li.job-box a.job-item"):
        text = clean(a.get_text(" "))
        if not text:
            continue
        badge_el = a.select_one("p.badge")
        badge = clean(badge_el.get_text()) if badge_el else ""
        body = text[len(badge):].strip() if badge and text.startswith(badge) else text
        m = re.search(r"(\d{4}\.\d{2}\.\d{2})[^~]*~\s*(\d{4}\.\d{2}\.\d{2})", body)
        posted = deadline = ""
        title = body
        if m:
            posted = m.group(1).replace(".", "-")
            deadline = m.group(2).replace(".", "-")
            title = clean(body[:m.start()])
        if not title:
            continue
        uid = f"uou:{title[:40]}|{posted}"
        if uid in seen:
            continue
        seen.add(uid)
        out.append({
            "id": uid,
            "source": "울산대학교",
            "source_kind": "대학 자체 채용",
            "url": home,
            "link_is_board": True,
            "title": title,
            "org": "울산대학교",
            "org_type": "대학·연구기관",
            "location": "울산",
            "category": "",
            "posted": posted,
            "deadline": deadline,
            "status": badge,
            "employment": classify_employment(title),
        })
    return out


def fetch_unist(days: int) -> list[dict]:
    """UNIST 채용공고 게시판."""
    base = "https://www.unist.ac.kr/unist/etc/notification/employment.do"
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for page in range(0, 9):
        r = get(base, params={"article.offset": str(page * 10), "articleLimit": "10"})
        soup = BeautifulSoup(r.text, "html.parser")
        table = pick_table(soup, ["제목", "작성일"])
        if table is None:
            break
        rows = table.find_all("tr")[1:]
        if not rows:
            break
        page_dates = []
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            a = tr.find("a", href=True)
            if not a:
                continue
            # 제목 칸에 작성일·조회수가 붙어 있어서 <a> 텍스트만 쓴다
            title = clean(a.get_text())
            title = re.sub(r"^(\[공지\]\s*)+", "", title).strip()
            if not title:
                continue
            posted = parse_date(clean(tds[3].get_text()))
            page_dates.append(posted)
            key = re.search(r"articleNo=(\d+)", a["href"])
            out.append({
                "id": f"unist:{key.group(1) if key else title[:24]}",
                "source": "UNIST",
                "source_kind": "대학 자체 채용",
                "url": urljoin(base, a["href"]),
                "title": title,
                "org": "울산과학기술원(UNIST)",
                "org_type": "대학·연구기관",
                "location": "울산",
                "category": "",
                "posted": posted,
                "deadline": "",
                "status": "",
                "employment": classify_employment(title),
            })
        if older_than(page_dates, cutoff):
            break
        time.sleep(0.3)
    return out


# ==================================================================
# main
# ==================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="조회 기간(일), 기본 60일")
    args = ap.parse_args()

    days = args.days
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    print(f"\n울산 공공기관 채용공고 수집  (최근 {days}일, {cutoff} 이후)")
    print("=" * 66)

    c = Collector(cutoff)
    c.run("잡알리오", lambda: fetch_alio(days), "중앙 공공기관")
    c.run("나라일터", lambda: fetch_gojobs(days), "국가·지자체·공공기관")
    c.run("클린아이 잡플러스", lambda: fetch_cleaneye(days), "지방공기업·출자출연")
    c.run("하이브레인넷", lambda: fetch_hibrain(days), "대학·연구기관")
    c.run("울산광역시", lambda: fetch_ulsan_city(days), "고시공고")
    c.run("울산 중구", lambda: fetch_junggu(days), "채용공고")
    for label, host, region, board in EMINWON_SITES:
        c.run(label, lambda l=label, h=host, rg=region, b=board: fetch_eminwon(l, h, rg, b, days),
              "새올 채용공고(링크는 게시판)")
    c.run("울주군", lambda: fetch_ulju(days), "채용공고")
    c.run("울산교육청 교육공무직", lambda: fetch_use_gongmujik(days))
    c.run("울산교육청 일반채용", lambda: fetch_use_general(days))
    c.run("울산교육청 온라인채용", lambda: fetch_use_online(days))
    c.run("춘해보건대학교", lambda: fetch_chunhae(days), "공지사항 중 채용글")
    c.run("울산과학대학교", lambda: fetch_ulsan_college(days), "채용공고")
    c.run("울산대학교", lambda: fetch_ulsan_univ(days), "교직원채용(링크는 시스템 첫화면)")
    c.run("UNIST", lambda: fetch_unist(days), "채용공고")

    # 중복 제거
    dedup, seen = [], set()
    for j in c.jobs:
        k = j.get("id") or j["title"]
        if k in seen:
            continue
        seen.add(k)
        dedup.append(j)

    dedup.sort(key=lambda x: (x.get("posted") or "", x.get("title") or ""), reverse=True)

    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "days": days,
        "cutoff": cutoff,
        "count": len(dedup),
        "sources": c.report,
        "jobs": dedup,
    }
    path = os.path.join(DATA_DIR, "jobs.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print("=" * 66)
    from collections import Counter
    types = Counter(j["employment"] for j in dedup)
    print(f"총 {len(dedup)}건 저장 → {path}")
    print("고용형태:", ", ".join(f"{k} {v}" for k, v in types.most_common()))
    target = sum(1 for j in dedup if j["is_target"])
    print(f"무기계약직·공무직·기간제: {target}건")


if __name__ == "__main__":
    main()
