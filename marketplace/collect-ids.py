#!/usr/bin/env python3
"""Collect plugin/version ids from marketplace.moodle.com.

Reads marketplace/manifest.json, for every unique component tries to find
the numeric plugin_id (JSON API candidates first, then the search page HTML),
then fetches the full version list (id + nearby "Moodle X.Y" mentions).

Outputs:
  marketplace/ids_report.json      - full data for manual version picking
  marketplace/manifest.updated.json- copy of the manifest with plugin_id filled in

Always exits 0; check the report for per-plugin failures.
"""
import json, os, re, sys, time, urllib.parse, urllib.request

BASE = os.environ.get("MOODLE_MARKETPLACE_BASE", "https://marketplace.moodle.com").rstrip("/")
COOKIE = os.environ.get("MOODLE_MARKETPLACE_COOKIE", "")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = int(os.environ.get("FETCH_TIMEOUT", "90"))
SLEEP = float(os.environ.get("FETCH_SLEEP", "2"))
HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.json")

def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json,*/*"})
    if COOKIE:
        req.add_header("Cookie", COOKIE)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, r.read().decode("utf-8", "replace")

def walk(obj, pred, out):
    if isinstance(obj, dict):
        if pred(obj):
            out.append(obj)
        for v in obj.values():
            walk(v, pred, out)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, pred, out)

NAME_KEYS = ("name", "component", "fullname", "title", "label", "plugin", "shortname")

def looks_like_plugin(d, component):
    if not isinstance(d.get("id"), int):
        return False
    for k in NAME_KEYS:
        v = d.get(k)
        if isinstance(v, str) and component.lower() in v.lower():
            return True
    return False

def looks_like_version(d):
    if not isinstance(d.get("id"), int):
        return False
    for k in ("label", "version", "release", "name", "title", "moodle_version", "summary"):
        if isinstance(d.get(k), str):
            return True
    return False

def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def find_plugin_id_html(component):
    url = BASE + "/plugins?search=" + urllib.parse.quote(component)
    try:
        status, html = http_get(url)
    except Exception as e:
        return None, "search page error: %s" % e
    for m in re.finditer(r'<a[^>]+href="[^"]*/plugins/(\d+)"[^>]*>(.*?)</a>', html, re.S):
        if component.lower() in strip_tags(m.group(2)).lower():
            return int(m.group(1)), "search page (name matched)"
    m = re.search(r"/plugins/(\d+)", html)
    if m:
        return int(m.group(1)), "search page (UNVERIFIED - first link on page)"
    return None, "search page: no /plugins/<id> link found (search URL may differ)"

def find_plugin_id_json(component):
    q = urllib.parse.quote(component)
    for url in (BASE + "/api/plugins?search=" + q,
                BASE + "/api/plugins/search?query=" + q,
                BASE + "/api/v1/plugins?search=" + q):
        try:
            status, body = http_get(url)
        except Exception:
            continue
        if status != 200:
            continue
        try:
            data = json.loads(body)
        except Exception:
            continue
        out = []
        walk(data, lambda d: looks_like_plugin(d, component), out)
        if out:
            return int(out[0]["id"]), "json api %s" % url
    return None, "json api: nothing found (endpoints may not exist)"

def find_versions_html(pid):
    url = BASE + "/plugins/%d/versions" % pid
    try:
        status, html = http_get(url)
    except Exception as e:
        return None, "versions page error: %s" % e
    versions = []
    seen = set()
    for m in re.finditer(r"/plugins/%d/versions/(\d+)" % pid, html):
        vid = int(m.group(1))
        if vid in seen:
            continue
        seen.add(vid)
        ctx = strip_tags(html[max(0, m.start() - 500): m.end() + 800])
        moodles = re.findall(r"Moodle\s+\d+\.\d+(?:[+-]\d*)?", ctx)
        versions.append({"version_id": vid, "moodle_mentions": moodles[:6], "context": ctx[:400]})
    if versions:
        return versions, "versions page (html)"
    return None, "versions page: no /versions/<id> links found"

def find_versions_json(pid):
    for url in (BASE + "/api/plugins/%d/versions" % pid,
                BASE + "/api/v1/plugins/%d/versions" % pid):
        try:
            status, body = http_get(url)
        except Exception:
            continue
        if status != 200:
            continue
        try:
            data = json.loads(body)
        except Exception:
            continue
        out = []
        walk(data, looks_like_version, out)
        if out:
            versions = []
            for d in out:
                label = ""
                for k in ("label", "version", "release", "name", "title"):
                    if isinstance(d.get(k), str):
                        label = d[k]
                        break
                moodles = []
                for v in d.values():
                    if isinstance(v, str):
                        moodles = re.findall(r"Moodle\s+\d+\.\d+(?:[+-]\d*)?", v)
                        if moodles:
                            break
                versions.append({"version_id": int(d["id"]), "label": label, "moodle_mentions": moodles[:6]})
            return versions, "json api %s" % url
    return None, "json api: no versions found"

def main():
    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    report = {}
    updated = json.loads(json.dumps(manifest))

    components = []
    for entries in manifest.values():
        for e in entries:
            if e["component"] not in components:
                components.append(e["component"])

    for c in components:
        time.sleep(SLEEP)
        info = {"component": c}
        pid = None
        for entries in manifest.values():
            for e in entries:
                if e["component"] == c and e.get("plugin_id"):
                    pid = e["plugin_id"]
        if pid is not None and not str(pid).isdigit():
            print("!! %s: non-numeric plugin_id %r in manifest - treated as missing" % (c, pid))
            pid = None
        info["plugin_id"] = pid
        if pid is None:
            pid, how = find_plugin_id_json(c)
            if pid is None:
                time.sleep(SLEEP)
                pid, how2 = find_plugin_id_html(c)
                info["plugin_discovery"] = "json: %s | html: %s" % (how, how2)
            else:
                info["plugin_discovery"] = how
        if pid is not None:
            info["plugin_id"] = pid
            for entries in updated.values():
                for e in entries:
                    if e["component"] == c and not e.get("plugin_id"):
                        e["plugin_id"] = pid
            time.sleep(SLEEP)
            vers, how = find_versions_json(pid)
            src = how
            if vers is None:
                time.sleep(SLEEP)
                vers, how2 = find_versions_html(pid)
                src = "%s | %s" % (how, how2)
            if vers:
                info["versions"] = vers
                info["versions_source"] = src
            else:
                info["versions_error"] = src
        else:
            info["plugin_error"] = info.get("plugin_discovery", "not found")
            print("!! %s: plugin id NOT FOUND: %s" % (c, info["plugin_error"]))
        report[c] = info
        print("-- %s: plugin_id=%s versions=%d" % (c, pid, len(info.get("versions", []))))

    with open(os.path.join(HERE, "ids_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    with open(os.path.join(HERE, "manifest.updated.json"), "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2, ensure_ascii=False)
    print("Wrote ids_report.json and manifest.updated.json")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FATAL: %s" % e)
        try:
            with open(os.path.join(HERE, "ids_report.json"), "w") as f:
                json.dump({"fatal": str(e)}, f)
        except Exception:
            pass
    sys.exit(0)
