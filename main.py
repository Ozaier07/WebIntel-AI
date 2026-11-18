"""WebIntel AI - backend (FastAPI). Run: uvicorn main:app --reload"""
import io, ipaddress, json, re, socket, sqlite3, time
from collections import Counter
from urllib.parse import urljoin, urlparse
from urllib import robotparser
import pandas as pd, requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB = "webintel.db"
UA = "Mozilla/5.0 (compatible; WebIntelAI-StudentProject/1.0)"
SAMPLE_URL = "sample"
SAMPLE_HTML = """<html><head><title>Intro to Cloud Computing - Course Page</title><meta name="description" content="A beginner course on cloud computing"></head><body><h1>Introduction to Cloud Computing</h1><h2>Course overview</h2><p>This course introduces students to cloud computing, covering software services, data storage, and web application deployment on modern cloud platforms.</p><p>Students learn how a distributed system handles data at scale, and how an API connects cloud services to every web application.</p><h2>Syllabus</h2><p>Topics include virtual machines, containers, cloud databases, security, and cost management for software teams.</p><a href="/week1">Week 1 notes</a><a href="https://example.org/lab">Lab guide</a><p class="price">Fee: Rs. 4,999</p></body></html>"""
app = FastAPI(title="WebIntel AI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])  # lets the HTML file work when opened directly

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

with db() as c:
    c.executescript("""
    create table if not exists projects(id integer primary key, name text, url text, fields text, created text default current_timestamp);
    create table if not exists jobs(id integer primary key, project_id int, status text, error text, secs real, count int default 0, created text default current_timestamp);
    create table if not exists records(id integer primary key, job_id int, field text, value text);
    create table if not exists analyses(job_id int primary key, summary text, keywords text, category text);""")

# ---- Stage 2: validate & fetch (SSRF protection, robots.txt, timeouts) ----
def check_url(u):
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Only http/https URLs are allowed")
    for info in socket.getaddrinfo(p.hostname, p.port or 80):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("Blocked: private/restricted address (SSRF protection)")

def robots_ok(u):
    p = urlparse(u); rp = robotparser.RobotFileParser()
    try:
        r = requests.get(f"{p.scheme}://{p.netloc}/robots.txt", timeout=5, headers={"User-Agent": UA})
        if r.status_code != 200: return True
        rp.parse(r.text.splitlines())
        return rp.can_fetch(UA, u)
    except requests.RequestException:
        return True

def fetch(u):
    try:
        for _ in range(4):  # redirects are re-validated every hop
            check_url(u)
            r = requests.get(u, timeout=15, headers={"User-Agent": UA, "Accept": "text/html,*/*"}, allow_redirects=False, stream=True)
            if r.is_redirect:
                u = urljoin(u, r.headers["Location"]); continue
            if r.status_code in (401, 403, 429):
                raise ValueError(f"The site refused the request (HTTP {r.status_code}). It may block automated access - try another page.")
            r.raise_for_status()
            data = b""
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > 2_000_000: break
            return u, data.decode(r.encoding or r.apparent_encoding or "utf-8", "ignore")
        raise ValueError("Too many redirects")
    except requests.exceptions.SSLError:
        raise ValueError("SSL certificate error while connecting to the site")
    except requests.exceptions.Timeout:
        raise ValueError("The site took too long to respond (timeout)")
    except requests.exceptions.ConnectionError:
        raise ValueError("Could not connect - check the URL and your internet connection")
    except requests.exceptions.HTTPError as e:
        raise ValueError(f"The site returned an error: {e.response.status_code}")
    except socket.gaierror:
        raise ValueError("Domain name not found - check the URL")

# ---- Stage 3: parse & clean ----
def extract(html, base, fields):
    s, out = BeautifulSoup(html, "html.parser"), []
    for f in fields:
        if f == "title": v = [s.title.get_text()] if s.title else []
        elif f == "headings": v = [h.get_text(" ") for h in s.find_all(["h1", "h2", "h3"])]
        elif f == "paragraphs": v = [p.get_text(" ") for p in s.find_all("p")]
        elif f == "links": v = [urljoin(base, a["href"]) for a in s.find_all("a", href=True)]
        elif f == "meta":
            v = [f'{m.get("name") or m.get("property")}: {m["content"]}' for m in s.find_all("meta", content=True) if m.get("name") or m.get("property")]
        elif f.startswith("css:"): v = [e.get_text(" ") for e in s.select(f[4:])]
        else: continue
        out += [(f, x) for x in v]
    df = pd.DataFrame(out, columns=["field", "value"])
    df["value"] = df["value"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    return df[df["value"].str.len() > 1].drop_duplicates().reset_index(drop=True)

# ---- Stage 4: analysis (local, offline). Swap in the Anakin AI call here once its API is verified. ----
STOP = set("the and for are was with that this from your you our not have has will can they their about more also been were which into than then".split())
CATS = {"Technology": "software data code system web api cloud", "Business": "market price company customer sales product", "Education": "course student learn university research study", "News": "said report government people year news"}

def analyse(texts):
    t = " ".join(texts)[:20000]
    words = [w for w in re.findall(r"[a-z]{4,}", t.lower()) if w not in STOP]
    freq = Counter(words)
    sents = [s for s in re.split(r"(?<=[.!?])\s+", t) if 40 < len(s) < 300]
    sc = lambda s: sum(freq[w] for w in re.findall(r"[a-z]{4,}", s.lower())) / len(s) ** .5
    summary = " ".join(sorted(sents, key=sc, reverse=True)[:3]) or "Not enough text to summarise."
    cat = max(CATS, key=lambda k: sum(freq[w] for w in CATS[k].split()))
    return summary, [w for w, _ in freq.most_common(8)], cat if freq else "Unknown"

# ---- API ----
class ProjectIn(BaseModel):
    name: str
    url: str
    fields: list[str]

@app.post("/api/projects")
def create_project(p: ProjectIn):
    if p.url != SAMPLE_URL:
        try: check_url(p.url)
        except socket.gaierror: raise HTTPException(400, "Domain name not found - check the URL")
        except Exception as e: raise HTTPException(400, str(e))
    with db() as c:
        cur = c.execute("insert into projects(name,url,fields) values(?,?,?)", (p.name, p.url, json.dumps(p.fields)))
        return {"id": cur.lastrowid}

@app.get("/api/projects")
def projects():
    with db() as c:
        return [dict(r) for r in c.execute("select p.*, (select count(*) from jobs j where j.project_id=p.id) runs from projects p order by id desc")]

@app.post("/api/projects/{pid}/run")
def run(pid: int):
    with db() as c:
        p = c.execute("select * from projects where id=?", (pid,)).fetchone()
        if not p: raise HTTPException(404, "Project not found")
        jid = c.execute("insert into jobs(project_id,status) values(?,'running')", (pid,)).lastrowid
    t0, err, df = time.time(), None, None
    try:
        if p["url"] == SAMPLE_URL:
            final, html = "https://sample.local/", SAMPLE_HTML
        else:
            if not robots_ok(p["url"]): raise ValueError("Blocked by robots.txt - scraping not permitted for this page")
            final, html = fetch(p["url"])
        df = extract(html, final, json.loads(p["fields"]))
    except Exception as e:
        err = str(e) or e.__class__.__name__
    with db() as c:
        if err:
            c.execute("update jobs set status='failed',error=?,secs=? where id=?", (err, time.time() - t0, jid))
        else:
            c.executemany("insert into records(job_id,field,value) values(?,?,?)", [(jid, r.field, r.value) for r in df.itertuples()])
            s, k, cat = analyse(df[df.field.isin(["paragraphs", "headings"])]["value"].tolist() or df["value"].tolist())
            c.execute("insert into analyses values(?,?,?,?)", (jid, s, json.dumps(k), cat))
            c.execute("update jobs set status='done',secs=?,count=? where id=?", (time.time() - t0, len(df), jid))
    return {"job_id": jid}

@app.get("/api/jobs/{jid}")
def job(jid: int):
    with db() as c:
        j = c.execute("select * from jobs where id=?", (jid,)).fetchone()
        if not j: raise HTTPException(404, "Job not found")
        a = c.execute("select * from analyses where job_id=?", (jid,)).fetchone()
        recs = [dict(r) for r in c.execute("select field,value from records where job_id=? limit 500", (jid,))]
    return {"job": dict(j), "records": recs, "analysis": dict(a) if a else None}

@app.get("/api/jobs/{jid}/export")
def export(jid: int, fmt: str = "csv"):
    with db() as c:
        df = pd.read_sql("select field,value from records where job_id=?", c, params=(jid,))
    if fmt == "json":
        return Response(df.to_json(orient="records"), media_type="application/json", headers={"Content-Disposition": f"attachment; filename=job{jid}.json"})
    return Response(df.to_csv(index=False), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=job{jid}.csv"})

@app.get("/api/jobs")
def jobs():
    with db() as c:
        return [dict(r) for r in c.execute("select j.*, p.name from jobs j join projects p on p.id=j.project_id order by j.id desc limit 50")]

app.mount("/", StaticFiles(directory="static", html=True))
