# -*- coding: utf-8 -*-
"""
sources_web.py — Web source resolvers (Wikimedia Commons, Pixabay, Openclipart)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, List
import json, hashlib, urllib.request, urllib.error, urllib.parse, ssl, os, re, sys
from pathlib import Path

import log as LOG
_l = LOG


@dataclass
class WebSources:
    assets_dir: Path

    # ---------------- Shared size parsing ----------------
    @staticmethod
    def _parse_int(v) -> int:
        try:
            return int(v)
        except Exception:
            return 0

    @staticmethod
    def _parse_size_spec(size: str) -> dict:
        """
        Shared size grammar for virtual web sources:
          - presets: tiny|small|medium|large|xlarge|largest
          - numeric minimum: N
          - numeric minimum box: WxH
          - aliases: s/m/l/high/original/full/orig/o
        """
        s = str(size or "").strip().lower()
        if not s:
            s = "medium"

        aliases = {
            "s": "small",
            "m": "medium",
            "l": "large",
            "h": "xlarge",
            "high": "xlarge",
            "orig": "largest",
            "original": "largest",
            "full": "largest",
            "o": "largest",
        }
        s = aliases.get(s, s)

        m_box = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", s)
        if m_box:
            return {
                "kind": "min_box",
                "min_w": max(1, int(m_box.group(1))),
                "min_h": max(1, int(m_box.group(2))),
                "label": s,
            }

        if re.match(r"^\d+$", s):
            n = max(1, int(s))
            return {"kind": "min_side", "min_side": n, "label": s}

        if s in ("tiny", "small", "medium", "large", "xlarge", "largest"):
            return {"kind": "preset", "preset": s, "label": s}

        # Backward-compatible fallback for unknown labels.
        return {"kind": "preset", "preset": "medium", "label": "medium"}

    # ---------------- Wikimedia Commons ----------------
    @staticmethod
    def _parse_wkmc_expr(expr: str) -> Optional[Tuple[str, str]]:
        s = (expr or "").strip()
        if not s.lower().startswith("wkmc://"):
            return None
        body = s[len("wkmc://"):].strip()
        if not body:
            return None
        query = ""
        size = "medium"
        if body and body[0] in ("'", '"'):
            qch = body[0]
            i = 1
            esc = False
            qbuf = []
            while i < len(body):
                ch = body[i]
                if esc:
                    qbuf.append(ch); esc = False; i += 1; continue
                if ch == "\\":
                    esc = True; i += 1; continue
                if ch == qch:
                    i += 1; break
                qbuf.append(ch); i += 1
            query = "".join(qbuf).strip()
            rest = body[i:].strip()
        else:
            parts = body.split("/", 1)
            query = parts[0].strip()
            rest = parts[1].strip() if len(parts) > 1 else ""
        if rest.startswith("/"):
            rest = rest[1:].strip()
        if rest:
            size = rest.strip()
        return (query, size)

    @staticmethod
    def _wkmc_thumb_constraints(size: str) -> Tuple[Optional[int], Optional[int]]:
        spec = WebSources._parse_size_spec(size)
        if spec.get("kind") == "min_box":
            return int(spec.get("min_w") or 1024), int(spec.get("min_h") or 1024)
        if spec.get("kind") == "min_side":
            v = int(spec.get("min_side") or 1024)
            return v, None
        if spec.get("kind") == "preset":
            p = str(spec.get("preset") or "medium")
            if p == "largest":
                return None, None
            px = {
                "tiny": 256,
                "small": 512,
                "medium": 1024,
                "large": 1536,
                "xlarge": 2048,
            }.get(p, 1024)
            return px, None
        return 1024, None

    @staticmethod
    def _wkmc_query_mode(query: str) -> Tuple[str, str]:
        q = str(query or "").strip()
        ql = q.lower()
        if ql.startswith("file:"):
            return "file", q
        if ql.startswith("category:"):
            return "category", q
        return "search", q

    def _wkmc_cache_file(self, query: str, size: str) -> Path:
        k = hashlib.sha256(f"{query}|{size}".encode("utf-8")).hexdigest()
        return self.assets_dir / f"wkmc_{k}.json"

    def _wkmc_fetch_urls(self, query: str, size: str) -> List[str]:
        out: List[str] = []
        req_w, req_h = self._wkmc_thumb_constraints(size)
        mode, qv = self._wkmc_query_mode(query)
        cont = None
        seen = set()
        ssl_unverified = False

        def _open_json(req: urllib.request.Request) -> dict:
            nonlocal ssl_unverified
            try:
                if ssl_unverified:
                    ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                        return json.loads(resp.read().decode("utf-8", errors="replace"))
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8", errors="replace"))
            except Exception as ex:
                msg = str(ex)
                if (not ssl_unverified) and ("CERTIFICATE_VERIFY_FAILED" in msg):
                    _l.w("[sources] wkmc SSL verify failed; retrying unverified TLS")
                    ssl_unverified = True
                    ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                        return json.loads(resp.read().decode("utf-8", errors="replace"))
                raise

        for _ in range(0, 12):  # max 12 pages
            params = {
                "action": "query",
                "format": "json",
                "prop": "imageinfo",
                "iiprop": "url|size",
                "iiurlwidth": str(req_w) if req_w else None,
                "iiurlheight": str(req_h) if req_h else None,
            }

            if mode == "file":
                title = qv if qv.lower().startswith("file:") else f"File:{qv}"
                params["titles"] = title
            elif mode == "category":
                ctitle = qv if qv.lower().startswith("category:") else f"Category:{qv}"
                params.update({
                    "generator": "categorymembers",
                    "gcmtitle": ctitle,
                    "gcmtype": "file",
                    "gcmnamespace": "6",
                    "gcmlimit": "50",
                })
                if cont:
                    params["gcmcontinue"] = str(cont)
            else:
                params.update({
                    "generator": "search",
                    "gsrsearch": qv,
                    "gsrnamespace": "6",   # File:
                    "gsrlimit": "50",
                })
                if cont:
                    params["gsroffset"] = str(cont)

            # remove None
            params = {k: v for k, v in params.items() if v is not None}
            url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": "PnPInk"})
            data = _open_json(req)
            pages = ((data or {}).get("query") or {}).get("pages") or {}
            for _pid, pg in pages.items():
                ii = (pg or {}).get("imageinfo") or []
                if not ii:
                    continue
                ii0 = ii[0] or {}
                url0 = ii0.get("thumburl") or ii0.get("url")
                if not url0:
                    continue
                if url0 in seen:
                    continue
                seen.add(url0)
                out.append(url0)
            cobj = (data or {}).get("continue") or {}
            cont = cobj.get("gcmcontinue") if mode == "category" else cobj.get("gsroffset")
            if mode == "file":
                cont = None
            if not cont:
                break
        return out

    def resolve_wkmc_urls(self, expr: str) -> Optional[List[str]]:
        p = self._parse_wkmc_expr(expr)
        if p is None:
            return None
        query, size = p
        cfile = self._wkmc_cache_file(query, size)
        try:
            if cfile.is_file():
                raw = json.loads(cfile.read_text(encoding="utf-8"))
                urls = list((raw or {}).get("urls") or [])
                urls = [str(u).strip() for u in urls if str(u or "").strip()]
                if urls:
                    _l.i(f"[sources] wkmc cache hit query='{query}' size='{size}' n={len(urls)}")
                    return urls
        except Exception:
            pass
        try:
            urls = self._wkmc_fetch_urls(query, size)
            cfile.write_text(json.dumps({"query": query, "size": size, "urls": urls}, ensure_ascii=False), encoding="utf-8")
            _l.i(f"[sources] wkmc fetched query='{query}' size='{size}' n={len(urls)}")
            return urls
        except Exception as ex:
            _l.w(f"[sources] wkmc fetch failed '{expr}': {ex}")
            return []

    # ---------------- Pixabay ----------------
    @staticmethod
    def _parse_pxby_expr(expr: str) -> Optional[Tuple[str, str]]:
        s = (expr or "").strip()
        if not s.lower().startswith("pxby://"):
            return None
        body = s[len("pxby://"):].strip()
        if not body:
            return None
        query = ""
        size = "medium"
        if body and body[0] in ("'", '"'):
            qch = body[0]
            i = 1
            esc = False
            qbuf = []
            while i < len(body):
                ch = body[i]
                if esc:
                    qbuf.append(ch); esc = False; i += 1; continue
                if ch == "\\":
                    esc = True; i += 1; continue
                if ch == qch:
                    i += 1; break
                qbuf.append(ch); i += 1
            query = "".join(qbuf).strip()
            rest = body[i:].strip()
        else:
            parts = body.split("/", 1)
            query = parts[0].strip()
            rest = parts[1].strip() if len(parts) > 1 else ""
        if rest.startswith("/"):
            rest = rest[1:].strip()
        if rest:
            size = rest.strip()
        return (query, size)

    @staticmethod
    def _pxby_pick_url(hit: dict, size: str) -> Optional[str]:
        spec = WebSources._parse_size_spec(size)
        cands = []

        def _add(url_key: str, w_key: str, h_key: str, rank: int):
            u = str((hit or {}).get(url_key) or "").strip()
            if not u:
                return
            w = WebSources._parse_int((hit or {}).get(w_key))
            h = WebSources._parse_int((hit or {}).get(h_key))
            cands.append((u, w, h, rank))

        # Ordered from smaller to bigger.
        _add("previewURL", "previewWidth", "previewHeight", 1)
        _add("webformatURL", "webformatWidth", "webformatHeight", 2)
        _add("largeImageURL", "imageWidth", "imageHeight", 3)
        _add("imageURL", "imageWidth", "imageHeight", 4)
        if not cands:
            return None

        def _meets(c, min_w: int, min_h: int, min_side: int) -> bool:
            _, w, h, _ = c
            if min_w > 0 and w > 0 and w < min_w:
                return False
            if min_h > 0 and h > 0 and h < min_h:
                return False
            if min_side > 0:
                m = max(w, h)
                if m > 0 and m < min_side:
                    return False
            return True

        # Map presets to minimum side so behavior is shared with future wrappers.
        min_side_map = {
            "tiny": 150,
            "small": 640,
            "medium": 960,
            "large": 1280,
            "xlarge": 1920,
        }

        min_w = 0
        min_h = 0
        min_side = 0
        if spec.get("kind") == "min_box":
            min_w = int(spec.get("min_w") or 0)
            min_h = int(spec.get("min_h") or 0)
        elif spec.get("kind") == "min_side":
            min_side = int(spec.get("min_side") or 0)
        elif spec.get("kind") == "preset":
            p = str(spec.get("preset") or "medium")
            if p == "largest":
                # Prefer largest available
                return sorted(cands, key=lambda t: (t[3], max(t[1], t[2])))[-1][0]
            min_side = int(min_side_map.get(p, 960))

        good = [c for c in cands if _meets(c, min_w, min_h, min_side)]
        if good:
            # Smallest candidate that still satisfies constraints.
            best = sorted(good, key=lambda t: (max(t[1], t[2]) if (t[1] or t[2]) else 10**9, t[3]))[0]
            return best[0]

        # Fallback to the largest available.
        return sorted(cands, key=lambda t: (max(t[1], t[2]), t[3]))[-1][0]

    def _pxby_cache_file(self, query: str, size: str) -> Path:
        k = hashlib.sha256(f"{query}|{size}".encode("utf-8")).hexdigest()
        return self.assets_dir / f"pxby_{k}.json"

    @staticmethod
    def _pixabay_key() -> str:
        # Env vars first
        k = (
            os.getenv("PNPINK_PIXABAY_KEY")
            or os.getenv("PIXABAY_API_KEY")
        )
        if k:
            return str(k).strip()
        # Then user-scoped key file (same pattern as gsheets tokens)
        kf = WebSources._pixabay_keyfile_path()
        if kf and os.path.isfile(kf):
            try:
                with open(kf, "r", encoding="utf-8") as f:
                    return str(f.read() or "").strip()
            except Exception:
                return ""
        return ""

    @staticmethod
    def _pixabay_keyfile_path() -> Optional[str]:
        # mirrors gsheets_client_pkce.py location pattern
        if sys.platform.startswith("win"):
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
            return os.path.join(base, "PnPInk", "pixabay", "key.txt")
        elif sys.platform == "darwin":
            return os.path.join(os.path.expanduser("~/Library/Application Support"), "PnPInk", "pixabay", "key.txt")
        else:
            return os.path.join(os.path.expanduser("~/.pnpink"), "pixabay", "key.txt")

    def _pxby_fetch_urls(self, query: str, size: str) -> List[str]:
        key = self._pixabay_key()
        if not key:
            _l.w("[sources] pxby: missing API key (set PNPINK_PIXABAY_KEY/PIXABAY_API_KEY or key file)")
            return []
        base = "https://pixabay.com/api/"
        ssl_unverified = False

        def _call(params: dict) -> dict:
            nonlocal ssl_unverified
            url = base + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": "PnPInk"})
            try:
                if ssl_unverified:
                    ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                        return json.loads(resp.read().decode("utf-8", errors="replace"))
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8", errors="replace"))
            except Exception as ex:
                msg = str(ex)
                if (not ssl_unverified) and ("CERTIFICATE_VERIFY_FAILED" in msg):
                    _l.w("[sources] pxby SSL verify failed; retrying unverified TLS")
                    ssl_unverified = True
                    ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                        return json.loads(resp.read().decode("utf-8", errors="replace"))
                raise

        # Direct access: no spaces
        if not re.search(r"\s", query):
            # Try numeric ID first
            if re.fullmatch(r"\d+", query or ""):
                data = _call({"key": key, "id": query})
                hits = (data or {}).get("hits") or []
                if hits:
                    url0 = self._pxby_pick_url(hits[0], size)
                    return [url0] if url0 else []
            # Try if query returns a single hit
            data = _call({"key": key, "q": query, "per_page": 3})
            hits = (data or {}).get("hits") or []
            total = int((data or {}).get("totalHits") or 0)
            if total == 1 or len(hits) == 1:
                url0 = self._pxby_pick_url(hits[0], size)
                return [url0] if url0 else []

        # Regular search list
        out: List[str] = []
        seen = set()
        page = 1
        per_page = 200
        while page <= 12:
            data = _call({"key": key, "q": query, "per_page": per_page, "page": page})
            hits = (data or {}).get("hits") or []
            if not hits:
                break
            for h in hits:
                url0 = self._pxby_pick_url(h or {}, size)
                if not url0 or url0 in seen:
                    continue
                seen.add(url0)
                out.append(url0)
            total = int((data or {}).get("totalHits") or 0)
            if page * per_page >= total:
                break
            page += 1
        return out

    def resolve_pxby_urls(self, expr: str) -> Optional[List[str]]:
        p = self._parse_pxby_expr(expr)
        if p is None:
            return None
        query, size = p
        cfile = self._pxby_cache_file(query, size)
        try:
            if cfile.is_file():
                raw = json.loads(cfile.read_text(encoding="utf-8"))
                urls = list((raw or {}).get("urls") or [])
                urls = [str(u).strip() for u in urls if str(u or "").strip()]
                if urls:
                    _l.i(f"[sources] pxby cache hit query='{query}' size='{size}' n={len(urls)}")
                    return urls
        except Exception:
            pass
        try:
            urls = self._pxby_fetch_urls(query, size)
            cfile.write_text(json.dumps({"query": query, "size": size, "urls": urls}, ensure_ascii=False), encoding="utf-8")
            _l.i(f"[sources] pxby fetched query='{query}' size='{size}' n={len(urls)}")
            return urls
        except Exception as ex:
            _l.w(f"[sources] pxby fetch failed '{expr}': {ex}")
            return []

    # ---------------- Openclipart ----------------
    @staticmethod
    def _parse_oclp_expr(expr: str) -> Optional[Tuple[str, str]]:
        s = (expr or "").strip()
        if not s.lower().startswith("oclp://"):
            return None
        body = s[len("oclp://"):].strip()
        if not body:
            return None
        query = ""
        size = "medium"
        if body and body[0] in ("'", '"'):
            qch = body[0]
            i = 1
            esc = False
            qbuf = []
            while i < len(body):
                ch = body[i]
                if esc:
                    qbuf.append(ch); esc = False; i += 1; continue
                if ch == "\\":
                    esc = True; i += 1; continue
                if ch == qch:
                    i += 1; break
                qbuf.append(ch); i += 1
            query = "".join(qbuf).strip()
            rest = body[i:].strip()
        else:
            parts = body.split("/", 1)
            query = parts[0].strip()
            rest = parts[1].strip() if len(parts) > 1 else ""
        if rest.startswith("/"):
            rest = rest[1:].strip()
        if rest:
            size = rest.strip()
        return (query, size)

    @staticmethod
    def _oclp_extract_id(query: str) -> Optional[str]:
        q = str(query or "").strip()
        m = re.match(r"^id\s*:\s*(\d+)$", q, re.I)
        if m:
            return m.group(1)
        m = re.search(r"/detail/(\d+)(?:[/?#]|$)", q, re.I)
        if m:
            return m.group(1)
        if re.fullmatch(r"\d+", q):
            return q
        return None

    @staticmethod
    def _oclp_pick_url(item: dict, size: str) -> Optional[str]:
        spec = WebSources._parse_size_spec(size)
        it = item or {}

        def _s(k: str) -> str:
            return str(it.get(k) or "").strip()

        # Common keys seen in Openclipart payloads / detail objects.
        u_thumb = _s("png_thumb") or _s("svg.png_thumb")
        u_small = _s("png_small") or _s("svg.png_small")
        u_large = _s("png_large") or _s("svg.png_large")
        u_full = _s("png_full_lossy") or _s("png_full") or _s("svg.png_full_lossy") or _s("svg.png_full")
        u_svg = _s("svg") or _s("svg_url") or _s("url")

        # Fallback from nested "svg" dict/string encoded patterns.
        if (not u_svg) and isinstance(it.get("svg"), dict):
            u_svg = str((it.get("svg") or {}).get("url") or "").strip()

        # Candidate ladder from small to large.
        ladder = [u_thumb, u_small, u_large, u_full, u_svg]
        ladder = [u for u in ladder if u and re.match(r"^https?://", u, re.I)]
        if not ladder:
            return None

        if spec.get("kind") == "preset":
            p = str(spec.get("preset") or "medium")
            if p == "tiny":
                return ladder[0]
            if p == "small":
                return ladder[min(1, len(ladder) - 1)]
            if p == "medium":
                return ladder[min(2, len(ladder) - 1)]
            if p == "large":
                return ladder[min(3, len(ladder) - 1)]
            if p in ("xlarge", "largest"):
                return ladder[-1]

        if spec.get("kind") == "min_side":
            n = int(spec.get("min_side") or 0)
            if n <= 512:
                return ladder[min(1, len(ladder) - 1)]
            if n <= 1400:
                return ladder[min(3, len(ladder) - 1)]
            return ladder[-1]

        if spec.get("kind") == "min_box":
            mw = int(spec.get("min_w") or 0)
            mh = int(spec.get("min_h") or 0)
            n = max(mw, mh)
            if n <= 512:
                return ladder[min(1, len(ladder) - 1)]
            if n <= 1400:
                return ladder[min(3, len(ladder) - 1)]
            return ladder[-1]

        return ladder[min(2, len(ladder) - 1)]

    def _oclp_cache_file(self, query: str, size: str) -> Path:
        k = hashlib.sha256(f"{query}|{size}".encode("utf-8")).hexdigest()
        return self.assets_dir / f"oclp_{k}.json"

    def _oclp_fetch_urls(self, query: str, size: str) -> List[str]:
        out: List[str] = []
        seen = set()
        ssl_unverified = False

        def _call_json(url: str) -> dict:
            nonlocal ssl_unverified
            req = urllib.request.Request(url, headers={"User-Agent": "PnPInk"})
            try:
                if ssl_unverified:
                    ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                        return json.loads(resp.read().decode("utf-8", errors="replace"))
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8", errors="replace"))
            except Exception as ex:
                msg = str(ex)
                if (not ssl_unverified) and ("CERTIFICATE_VERIFY_FAILED" in msg):
                    _l.w("[sources] oclp SSL verify failed; retrying unverified TLS")
                    ssl_unverified = True
                    ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                        return json.loads(resp.read().decode("utf-8", errors="replace"))
                raise

        # Specific item by id (id:123, 123, or detail URL).
        qid = self._oclp_extract_id(query)
        if qid:
            data = {}
            try:
                data = _call_json(f"https://openclipart.org/detail/{qid}.json")
            except Exception:
                data = {}
            item = (data or {}).get("payload") or (data or {}).get("data") or data
            if isinstance(item, dict):
                u = self._oclp_pick_url(item, size)
                if u:
                    return [u]
            return []

        # Search list.
        page = 1
        while page <= 10:
            params = {"query": query, "amount": "100", "page": str(page)}
            url = "https://openclipart.org/search/json/?" + urllib.parse.urlencode(params)
            data = _call_json(url)
            payload = (data or {}).get("payload")
            if isinstance(payload, dict):
                items = list(payload.get("results") or payload.get("items") or [])
            elif isinstance(payload, list):
                items = payload
            else:
                items = list((data or {}).get("results") or [])
            if not items:
                break
            for it in items:
                if not isinstance(it, dict):
                    continue
                u = self._oclp_pick_url(it, size)
                if not u or (u in seen):
                    continue
                seen.add(u)
                out.append(u)
            if len(items) < 100:
                break
            page += 1
        return out

    def resolve_oclp_urls(self, expr: str) -> Optional[List[str]]:
        p = self._parse_oclp_expr(expr)
        if p is None:
            return None
        query, size = p
        cfile = self._oclp_cache_file(query, size)
        try:
            if cfile.is_file():
                raw = json.loads(cfile.read_text(encoding="utf-8"))
                urls = list((raw or {}).get("urls") or [])
                urls = [str(u).strip() for u in urls if str(u or "").strip()]
                if urls:
                    _l.i(f"[sources] oclp cache hit query='{query}' size='{size}' n={len(urls)}")
                    return urls
        except Exception:
            pass
        try:
            urls = self._oclp_fetch_urls(query, size)
            cfile.write_text(json.dumps({"query": query, "size": size, "urls": urls}, ensure_ascii=False), encoding="utf-8")
            _l.i(f"[sources] oclp fetched query='{query}' size='{size}' n={len(urls)}")
            return urls
        except Exception as ex:
            _l.w(f"[sources] oclp fetch failed '{expr}': {ex}")
            return []
