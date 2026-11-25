import base64
import hashlib
import http.client
import json
import random
import string
import struct
import zlib

URL = "https://tanks-dana-merely-scenes.trycloudflare.com/"
IPCT = 2000
WLOAD = 290
ST = [200, 300]
PV = [1, 3]
PV_ST = [10, 30]
VISITS = 20

CLIENT = "".join(random.choices(string.ascii_lowercase + string.digits, k=16))
SERVER = base64.b64decode(b"aHR0cDovL2MwLmlwdHMuY29tL3VybHN2YzU=").decode()
C2_HOSTS = json.loads(base64.b64decode(b"WyJjMC5pcHRzLmNvbSIsImMxLmlwdHMuY29tIiwiYzIuaXB0cy5jb20iLCJjMy5pcHRzLmNvbSJd").decode())

XOR_BASE = 0x12322
XOR_BYTES = 16
VER = 45
USER_AGENT = base64.b64decode(
    b"TW96aWxsYS81LjAgKFdpbmRvd3MgTlQgMTApIEFwcGxlV2ViS2l0LzUzNy4zNiAoS0hUTUwsIGxpa2UgR2Vja28pIGlwdHMvOC4yLjEuNDU="
).decode()
TIMEOUT = 15.0
X_SATURATE_AT = 0x19000
X_SATURATED = 0xFFC00000


def make_identity(client):
    h = hashlib.sha256(client.encode()).digest()
    return h[:12].hex(), h[12:28].hex()


def with_query(url):
    return url + ("&" if "?" in url else "?") + CLIENT


def xor_first16(data):
    out = bytearray(data)
    key = XOR_BASE
    for i in range(0, min(XOR_BYTES, len(out)) - 3, 4):
        v = struct.unpack_from("<I", out, i)[0] ^ key
        struct.pack_into("<I", out, i, v & 0xFFFFFFFF)
        key += XOR_BASE
    return bytes(out)


def encrypt_req(obj):
    plain = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()
    return xor_first16(zlib.compress(plain, 1))


def decrypt_resp(body):
    try:
        return json.loads(zlib.decompress(body))
    except zlib.error:
        text = body.strip()
        if text[:1] == b"{":
            return json.loads(text)
        if text[:1] == b"-":
            return {"err": int(text)}
        raise ValueError("unable to parse response")


def pack_x(active_ms, flag=0):
    t = ((int(active_ms) + 50) * 0x51EB851F) >> 32
    if int(active_ms) + 50 >= X_SATURATE_AT:
        x = X_SATURATED
    else:
        x = ((t & ~0x1F) << 17) & 0xFFFFFFFF
    return x | (int(flag) & 0xFFFFFFFF)


def flag_for_task(task, main_url=""):
    ttype = (task.get("binfo") or {}).get("type")
    if ttype == 3:
        return 0x08
    if ttype == 11 or "google." in (main_url or ""):
        return 0x82
    return 0x02


def simulate_task(task, wave_id):
    binfo = task.get("binfo") or {}
    trules = task.get("trules") or []
    main = next((r for r in trules if r.get("tmt") == 32), {})
    sub = next((r for r in trules if r.get("tmt") == 33), {})
    main_url = main.get("url", "")
    st = main.get("st") or [10, 30]
    pv = sub.get("pv") or [1, 1]

    stay_sec = random.uniform(st[0], st[1])
    rate = 0.30 if "google." in main_url else 0.90

    ok = failed = subpages = 0
    total_bytes = total_ms = 0
    for _ in range(VISITS):
        if random.random() < rate:
            ok += 1
        else:
            failed += 1
        pvn = random.randint(pv[0], pv[1])
        subpages += pvn
        total_bytes += random.randint(600, 40000) + pvn * random.randint(300, 20000)
        total_ms += random.randint(250, 2500) + pvn * random.randint(150, 1200)

    return {
        "uid": binfo.get("uid", 0),
        "status": 200 if rate >= 0.5 else 429,
        "ok": ok, "failed": failed, "subpage": subpages,
        "total": VISITS + subpages,
        "bytes": total_bytes, "ms": total_ms,
        "active_ms": int(stay_sec * 1000),
        "flag": flag_for_task(task, main_url),
        "counter": random.randint(10, 80),
        "wave": wave_id & 0xFFFFFFFF,
    }


def build_report_entry(stat):
    ruids = [
        stat.get("uid", 0),
        pack_x(stat.get("active_ms", 0), stat.get("flag", 1)),
        stat.get("counter", 1),
    ]
    rueis = [
        stat.get("subpage", 0), 0,
        stat.get("total", 0),
        stat.get("failed", 0), stat.get("ok", 0),
        stat.get("bytes", 0), stat.get("ms", 0),
        stat.get("status", 0), stat.get("wave", 0),
    ]
    return ruids, rueis


class IptsClient:
    def __init__(self, base_url, cid24, cid32):
        self.cid24 = cid24
        self.cid32 = cid32
        self.ctm_idx = 0
        self.cid = 0

        scheme, _, rest = base_url.partition("://")
        hostport, _, base_path = rest.partition("/")
        host = hostport.split(":")[0]
        self._scheme = scheme
        self._port = 80 if scheme == "http" else 443
        self._base_path = "/" + base_path.rstrip("/")
        self._hosts = list(dict.fromkeys([host] + C2_HOSTS))
        self._host_idx = 0

    @property
    def host(self):
        return self._hosts[self._host_idx]

    def _rotate(self):
        self._host_idx = (self._host_idx + 1) % len(self._hosts)

    def post(self, payload):
        body = encrypt_req(payload)
        last_err = None
        for _ in range(len(self._hosts)):
            path = "%s/creq%d.%s" % (
                self._base_path, random.randrange(1000),
                random.choice(["js", "css", "html", "htm", "xml"])
            )
            try:
                if self._scheme == "https":
                    conn = http.client.HTTPSConnection(self.host, self._port, timeout=TIMEOUT)
                else:
                    conn = http.client.HTTPConnection(self.host, self._port, timeout=TIMEOUT)
                try:
                    conn.request("POST", path, body=body, headers={
                        "Content-Type": "application/octet-stream",
                        "User-Agent": USER_AGENT,
                    })
                    resp = conn.getresponse()
                    status, raw = resp.status, resp.read()
                finally:
                    conn.close()
            except OSError as e:
                last_err = str(e)
                self._rotate()
                continue

            if status == 200:
                return decrypt_resp(raw)
            last_err = "HTTP %d" % status
            self._rotate()
        raise RuntimeError("request failed: %s" % last_err)

    def _base(self, f, **extra):
        p = {
            "cid": self.cid, "cid24": self.cid24, "cid32": self.cid32,
            "f": f, "mode": 0, "tz": 8, "ver": VER, "wver": 0,
        }
        p.update(extra)
        return p

    def register(self):
        payload = self._base(10, cid=0, cfgid=0, luat=0, mbdid=0,
                             sysinfo={"cpu": 8, "cpumz": 3200, "iever": 105,
                                      "lang": 2052, "mem": 16384, "os": 100, "tz": 8})
        result = self.post(payload)
        cid = result.get("cid")
        if not isinstance(cid, int) or cid <= 0:
            raise RuntimeError("registration failed: %s" % result)
        self.cid = cid
        return result

    def add_site(self, url):
        trules = [
            {"av": 0, "ct": 1, "rate": 20, "st": [0, 0], "tmt": 34, "use": 0},
            {"srcs": [], "tmt": 31, "use": 0},
            {"st": list(ST), "tmt": 32, "url": url, "use": 1},
            {"av": 0, "clks": [], "pself": 0, "st": [0, 0], "tmt": 35, "use": 0},
            {"mi": "", "pv": list(PV), "st": list(PV_ST), "tmt": 33, "type": 0, "use": 1},
        ]
        payload = self._base(20, umt=6, data={
            "area": [],
            "binfo": {"ipct": IPCT, "type": 1, "ver": 37, "wload": WLOAD},
            "htct": [],
            "trules": trules,
            "ua": {"mrate": 0, "stype": 1, "uas": [], "use": 1},
        })
        result = self.post(payload)
        if result.get("err") not in (0, None):
            raise RuntimeError("failed to add site: %s" % result)
        urlid = result.get("urlid")
        if not isinstance(urlid, int) or urlid <= 0:
            raise RuntimeError("add site returned no urlid: %s" % result)
        return urlid, result

    def get_tasks(self, quids=None):
        self.ctm_idx += 1
        extra = {"atype": 4, "ctm_idx": self.ctm_idx}
        if quids:
            extra["quids"] = list(quids)
        return self.post(self._base(30, **extra))

    def report(self, entries, quids=None):
        self.ctm_idx += 1
        ruids, rueis = [], []
        for e in entries:
            r, x = build_report_entry(e)
            ruids += r
            rueis.append(x)
        payload = self._base(30, atype=5, ctm_idx=self.ctm_idx,
                             rueis=rueis, ruids=ruids)
        if quids:
            payload["quids"] = list(quids)
        return self.post(payload)


def main():
    cid24, cid32 = make_identity(CLIENT)
    client = IptsClient(SERVER, cid24, cid32)

    reg = client.register()
    print("cid =", client.cid)

    add_url = with_query(URL)
    urlid, add_resp = client.add_site(add_url)
    print("urlid =", urlid)

    tasks_resp = client.get_tasks(quids=[urlid])
    tasks = tasks_resp.get("tasks") or []
    print("tasks:", len(tasks))

    if tasks:
        wave = random.getrandbits(32)
        entries = [simulate_task(t, wave) for t in tasks]
        client.report(entries, quids=[urlid])
        print("YES")
    else:
        print("NO")


if __name__ == "__main__":
    main()