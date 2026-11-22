# -*- coding: utf-8 -*-
"""미니메모 Pro 라이선스 코어.

라이선스 상태 판정의 단일 진입점. 앱(mini_memo.py)의 상태 표시와
MCP 서버(minimemo_mcp.py)·CLI(minimemo_ai.py)의 게이트가 전부
이 모듈을 거친다.

확정 모델 (2026-11-04 상품화 결론, 2026-11-06 게이트 범위 확정):
- 메모 앱 본체 기능은 버전에 관계없이 전부 무료다. 이 모듈은 앱 본체에
  어떤 제한도 걸지 않고, AI 연동(MCP·CLI)의 범위만 판정한다.
- MCP·CLI 읽기: 무료. 단, 복수 연결 게이트는 읽기에도 적용된다.
- MCP·CLI 쓰기와 복수 AI 연결: Pro (Microsoft 스토어 애드온, 일회성).
- Pro 14일 전체 체험: 첫 라이선스 확인 시점부터 14일간 Pro와 동일하다.

상태 3종: pro / trial(체험 중, 잔여일 포함) / free(체험 종료).

판정 소스 (우선순위 순):
  1. 환경변수 오버라이드 (테스트용): MINIMEMO_LICENSE_STATE=pro|trial|free
     (trial이면 MINIMEMO_LICENSE_DAYS_LEFT로 잔여일을 줄 수 있다)
  2. 로컬 라이선스 파일 license.json (dogfood·수동 부여용):
     {"pro": true, "granted_at": "...", "note": "..."} 형태면 Pro.
  3. Windows 스토어 라이선스: STORE_ADDON_ID가 설정된 패키지 환경에서만
     동작한다. 애드온 상품이 아직 생성되지 않아 현재는 항상 "없음"으로
     안전하게 떨어진다.
  4. 체험 기록 trial.json: 없으면 만들고(첫 실행으로 간주), 있으면
     경과 일수로 판정한다.

신뢰 기반 게이트다 (DRM이 아니다): 기록 파일(license.json·trial.json·
connections.json)은 전부 데이터 폴더의 평문 JSON이라, 사용자가 파일을
지우거나 고치면 우회할 수 있다. $9.99 일회성 상품에서 그 이상의 보호는
과잉이라는 확정 판단(release/packaging_plan.md)을 따르며, 문안에서도
DRM인 것처럼 표기하지 않는다.

fail-open (PM 확정): 판정 코드가 예외를 내면 접근을 막지 않고
license.log에 기록만 남긴다. 라이선스 쪽 버그가 사용자의 메모 접근을
막는 사고가 과금 누락보다 크다고 보기 때문이다.
"""
import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta

TRIAL_DAYS = 14
MULTI_WINDOW_DAYS = 7

# Partner Center에서 Pro 애드온 상품을 만들면 그 상품의 스토어 ID를
# 여기에 넣는다. 비어 있는 동안 스토어 소스는 "없음"으로 떨어진다.
STORE_ADDON_ID = "9P3J9LW5S008"

LICENSE_FILE = "license.json"
TRIAL_FILE = "trial.json"
CONNECTIONS_FILE = "connections.json"
LOG_FILE = "license.log"

MSG_WRITE_BLOCKED = (
    "이 작업은 미니메모 Pro 기능입니다. 14일 무료 체험이 종료되어 "
    "AI 쓰기(메모 추가·보관·활성화)가 잠겼습니다. 메모 읽기와 검색은 "
    "계속 무료입니다. 사용자에게 미니메모 Pro(일회성 구매)를 안내해 주세요."
)
MSG_MULTI_BLOCKED = (
    "무료 버전에서는 AI를 1개만 연결할 수 있습니다. 이미 다른 AI가 먼저 "
    "연결되어 있어 이 AI의 접근(읽기 포함)이 차단됐습니다. 여러 AI를 함께 "
    "쓰려면 미니메모 Pro(일회성 구매)가 필요합니다. 사용자에게 이 사실을 "
    "알려 주세요."
)


# ---------- 공용 ----------

def _data_dir(data_file=None):
    if data_file:
        return os.path.dirname(os.path.abspath(data_file))
    from minimemo_core import resolve_data_file
    return os.path.dirname(resolve_data_file())


def _log(data_dir, msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    try:
        print("minimemo_license: " + line, file=sys.stderr)
    except Exception:
        pass
    try:
        with open(os.path.join(data_dir, LOG_FILE), "a",
                  encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _read_json(path):
    try:
        # utf-8-sig: PowerShell Set-Content -Encoding UTF8가 붙이는 BOM도 허용
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_json(path, obj):
    # 코어(save_payload)와 같은 방식: writer별 고유 임시 파일 + 원자적 교체.
    tmp = f"{path}.lic-tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


# ---------- 판정 소스 ----------

def _env_override():
    raw = (os.environ.get("MINIMEMO_LICENSE_STATE") or "").strip().lower()
    if raw == "pro":
        return {"state": "pro", "source": "env", "trial_days_left": None}
    if raw == "free":
        return {"state": "free", "source": "env", "trial_days_left": 0}
    if raw == "trial":
        try:
            left = int(os.environ.get("MINIMEMO_LICENSE_DAYS_LEFT")
                       or TRIAL_DAYS)
        except ValueError:
            left = TRIAL_DAYS
        return {"state": "trial", "source": "env",
                "trial_days_left": max(0, left)}
    return None


def _local_grant(data_dir):
    """로컬 라이선스 파일(license.json)에 Pro 부여가 있는지 본다.

    dogfood·수동 부여용이다. 형식: {"pro": true, ...}. 파일이 없거나
    pro가 참이 아니면 부여 없음으로 본다.
    """
    data = _read_json(os.path.join(data_dir, LICENSE_FILE))
    return bool(data and data.get("pro"))


def _store_has_pro(data_dir):
    """Windows 스토어 라이선스로 Pro 애드온 보유를 확인한다.

    반환: True(보유 확정) / False(미보유 확정) / None(확인 불가).
    STORE_ADDON_ID가 비어 있으면(애드온 미생성) 조회 자체를 하지 않고
    None을 돌려준다. 실제 WinRT 조회 경로는 Windows 패키지 환경에서만
    동작하며 이 개발 환경에서는 실행 검증이 불가능하다 (미검증 경로).
    어떤 실패도 None으로 떨어뜨려 다른 소스 판정을 막지 않는다.
    """
    if not STORE_ADDON_ID:
        return None
    if os.name != "nt":
        return None
    try:
        return _store_has_pro_winrt()
    except Exception as e:
        _log(data_dir, f"store license check failed: {e!r}")
        return None


def _store_has_pro_winrt():
    """WinRT Store API로 앱 라이선스의 애드온 보유를 확인한다 (미검증).

    python용 WinRT 프로젝션 패키지(winrt-Windows.Services.Store)가
    필요하다. GetAppLicenseAsync는 비동기라, 호출자 쪽 이벤트 루프
    (MCP 서버의 asyncio 등)와 부딪히지 않게 별도 스레드의 자체
    루프에서 실행한다.
    """
    import asyncio
    import threading

    from winrt.windows.services.store import StoreContext

    result = {}

    async def _fetch():
        ctx = StoreContext.get_default()
        return await ctx.get_app_license_async()

    def _run():
        try:
            result["license"] = asyncio.run(_fetch())
        except Exception as e:  # 스레드 안 예외는 호출자에게 직접 못 던진다
            result["error"] = e

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout=15)
    if "error" in result or "license" not in result:
        raise RuntimeError(f"store license query failed: {result.get('error')!r}")
    addons = result["license"].add_on_licenses
    try:
        addon = addons.lookup(STORE_ADDON_ID)
    except Exception:
        return False  # 맵에 없으면 미보유 확정
    return bool(addon and addon.is_active)


def _trial_state(data_dir, today):
    """체험 기록을 읽어 (체험 중인지, 잔여일)을 돌려준다.

    기록이 없으면 오늘을 첫 실행일로 기록한다. 시스템 시계가 과거로
    가도 체험이 늘어나지 않게, 판정 기준일은 max(오늘, 마지막 확인일)로
    단조롭게 잡고 last_seen은 뒤로 되돌리지 않는다.
    """
    path = os.path.join(data_dir, TRIAL_FILE)
    rec = _read_json(path)
    first = last = None
    if rec:
        try:
            first = date.fromisoformat(rec["first_run"])
            last = date.fromisoformat(rec["last_seen"])
        except Exception:
            first = last = None
    if first is None:
        rec = {"first_run": today.isoformat(), "last_seen": today.isoformat()}
        _write_json(path, rec)
        return True, TRIAL_DAYS
    if last < today:
        rec = dict(rec)
        rec["last_seen"] = today.isoformat()
        _write_json(path, rec)
        last = today
    effective = max(today, last)
    days_used = (effective - first).days
    left = TRIAL_DAYS - days_used
    return left > 0, max(0, left)


# ---------- 상태 판정 (단일 진입점) ----------

def get_status(data_file=None, today=None):
    """현재 라이선스 상태를 돌려준다.

    반환 dict: {"state": "pro"|"trial"|"free",
                "source": "env"|"file"|"store"|"trial"|"none",
                "trial_days_left": int|None}
    today는 테스트용 주입 지점이다 (date 객체).
    """
    data_dir = _data_dir(data_file)
    ov = _env_override()
    if ov is not None:
        return ov
    if _local_grant(data_dir):
        return {"state": "pro", "source": "file", "trial_days_left": None}
    if _store_has_pro(data_dir):
        return {"state": "pro", "source": "store", "trial_days_left": None}
    active, left = _trial_state(data_dir, today or date.today())
    if active:
        return {"state": "trial", "source": "trial",
                "trial_days_left": left}
    return {"state": "free", "source": "none", "trial_days_left": 0}


def status_text(data_file=None):
    """앱 표시용 한 줄 상태 문구."""
    st = get_status(data_file)
    if st["state"] == "pro":
        return "Pro"
    if st["state"] == "trial":
        return f"Pro 체험 중 · {st['trial_days_left']}일 남음"
    return "무료 (체험 종료)"


def dashboard_text(data_file=None):
    """대시보드에 그대로 붙이는 라이선스 안내 한 줄.

    구매 동선은 스토어 링크가 아직 없으므로 안내 문구까지만 둔다.
    메모 본체 기능은 버전에 관계없이 전부 무료라는 점도 함께 밝힌다.
    """
    st = get_status(data_file)
    if st["state"] == "pro":
        return "AI 연결 라이선스: Pro"
    if st["state"] == "trial":
        return (f"AI 연결 라이선스: Pro 체험 중 · {st['trial_days_left']}일 남음"
                " · 체험이 끝나도 메모 기능과 AI 읽기는 계속 무료입니다")
    return ("AI 연결 라이선스: 무료 · AI 쓰기와 복수 연결은 Pro 기능입니다"
            " (Pro 구매는 Microsoft 스토어 출시 후 안내합니다)")


# ---------- 복수 연결 레지스트리 ----------

def register_connection(data_file, ai_name, now=None):
    """이 AI의 활동을 연결 레지스트리에 기록한다 (등장 순서 유지).

    first_seen은 처음 등장한 시각으로 고정하고 last_seen만 갱신한다.
    복수 연결 판정의 "먼저 등장한 이름"은 이 first_seen 순서를 따른다.
    """
    data_dir = _data_dir(data_file)
    now = now or datetime.now()
    path = os.path.join(data_dir, CONNECTIONS_FILE)
    data = _read_json(path) or {}
    conns = data.get("connections")
    if not isinstance(conns, list):
        conns = []
    stamp = now.isoformat(timespec="seconds")
    for c in conns:
        if isinstance(c, dict) and c.get("ai_name") == ai_name:
            c["last_seen"] = stamp
            break
    else:
        conns.append({"ai_name": ai_name, "first_seen": stamp,
                      "last_seen": stamp})
    _write_json(path, {"connections": conns})


def _active_names(data_dir, now):
    """최근 MULTI_WINDOW_DAYS일 안에 활동한 AI 이름들을 등장 순으로."""
    data = _read_json(os.path.join(data_dir, CONNECTIONS_FILE)) or {}
    conns = data.get("connections")
    if not isinstance(conns, list):
        return []
    cutoff = now - timedelta(days=MULTI_WINDOW_DAYS)
    active = []
    for c in conns:
        if not isinstance(c, dict) or not c.get("ai_name"):
            continue
        try:
            last = datetime.fromisoformat(c.get("last_seen") or "")
        except ValueError:
            continue
        if last >= cutoff:
            active.append(c)
    # 등장 순 = 파일에 append된 순서다. first_seen은 초 단위라 같은 초에
    # 등록된 이름들은 동률인데, 여기서 이름순으로 한 번 더 정렬하면
    # 실제 등장 순서가 뒤집힌다 (2026-11-09 실측: SoloAI가 먼저 등록
    # 됐는데 SecondAI가 1순위가 돼 첫 AI가 복수 연결로 차단됐다).
    # 안정 정렬로 동률에서는 파일 순서를 그대로 유지한다.
    active.sort(key=lambda c: c.get("first_seen") or "")
    return [c["ai_name"] for c in active]


# ---------- 게이트 ----------

def check_access(data_file, ai_name, write=False, today=None, now=None,
                 probe=False):
    """MCP·CLI 집행 지점이 부르는 게이트. 판정 결과 dict를 돌려준다.

    반환: {"allowed": bool,
           "reason": "ok"|"pro_required"|"multi"|"error",
           "message": str (차단 시 AI가 사용자에게 전할 안내 문장),
           "state": 판정에 쓴 라이선스 상태}

    규칙:
    - pro·trial이면 전부 허용한다 (체험은 Pro 전체 체험).
    - free이면 첫 번째로 등장한 AI만 읽기를 허용하고 쓰기는 막는다.
      두 번째 이후 AI는 읽기를 포함해 전부 막는다 (복수 연결 게이트).
    - 판정 중 예외가 나면 막지 않고 로그를 남긴다 (fail-open).

    probe=True는 온보딩 연결 테스트 전용 점검 모드다. 연결 레지스트리에
    이름을 남기지 않고(테스트가 실제 AI를 두 번째 연결로 밀어내면 안
    되므로), 복수 연결 순위도 매기지 않는다: pro·trial이면 전부 허용,
    free이면 읽기만 허용하고 쓰기는 pro_required로 막는다. 테스트는
    읽기만 수행하므로 이 판정으로 충분하다.
    """
    try:
        data_dir = _data_dir(data_file)
        st = get_status(data_file, today=today)
        if probe:
            if st["state"] in ("pro", "trial"):
                return {"allowed": True, "reason": "ok", "message": "",
                        "state": st["state"]}
            if write:
                return {"allowed": False, "reason": "pro_required",
                        "message": MSG_WRITE_BLOCKED, "state": "free"}
            return {"allowed": True, "reason": "ok", "message": "",
                    "state": "free"}
        register_connection(data_file, ai_name, now=now)
        if st["state"] in ("pro", "trial"):
            return {"allowed": True, "reason": "ok", "message": "",
                    "state": st["state"]}
        names = _active_names(data_dir, now or datetime.now())
        rank = names.index(ai_name) if ai_name in names else len(names)
        if rank >= 1:
            return {"allowed": False, "reason": "multi",
                    "message": MSG_MULTI_BLOCKED, "state": "free"}
        if write:
            return {"allowed": False, "reason": "pro_required",
                    "message": MSG_WRITE_BLOCKED, "state": "free"}
        return {"allowed": True, "reason": "ok", "message": "",
                "state": "free"}
    except Exception as e:
        try:
            _log(_data_dir(data_file), f"gate fail-open: {e!r}")
        except Exception:
            pass
        return {"allowed": True, "reason": "error", "message": "",
                "state": "unknown"}
