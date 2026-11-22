# -*- coding: utf-8 -*-
"""미니메모 데이터 코어.

notes.json의 로드/저장, 충돌 감지를 동반한 변경(mutate), 노트 규칙
(잠금 판정, 제목, 외부 공개 뷰, AI 노트 생성)을 한곳에 모은 모듈.
AI 연동 CLI(minimemo_ai.py)와 MCP 서버가 이 코어를 공유하고,
앱(mini_memo.py) 통합은 후속 단계에서 같은 코어로 붙인다.

규칙:
- 쓰기는 원자적 교체 + 읽은 시점의 mtime이 달라졌으면 쓰지 않고 재시도한다.
- 비밀번호 잠금 메모는 본문과 실제 제목을 외부에 돌려주지 않는다.
- AI가 만든 메모는 origin/ai_name/ai_at으로 출처를 남긴다.
"""
import json
import os
import random
import time
import uuid
from datetime import datetime

APP_NAME = "MiniMemo"


def local_settings():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    local = os.path.join(base, APP_NAME)
    try:
        with open(os.path.join(local, "settings.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def resolve_data_file(cli_dir=None):
    if cli_dir:
        return os.path.join(cli_dir, "notes.json")
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    local = os.path.join(base, APP_NAME)
    data_dir = local
    cfg = local_settings()
    if cfg.get("data_dir"):
        data_dir = cfg["data_dir"]
    return os.path.join(data_dir, "notes.json")


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def load_payload(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_payload(path, payload, expected_mtime):
    """읽은 시점의 mtime과 다르면(그 사이 앱이 저장했으면) 쓰지 않고 False.

    원자적 교체로 쓰며, 충돌 판단은 mutate가 다시 읽고 재시도하게 한다.
    임시 파일명은 writer별로 고유하게(pid+무작위 접미사) 만든다: 고정 이름을
    쓰면 동시 writer(MCP 서버 복수 개, CLI 동시 실행)가 같은 tmp를 두고
    충돌해 한쪽이 FileNotFoundError로 죽는다 (2026-11-05 침투 실측).
    성공·실패 어느 쪽에서도 임시 파일이 남지 않게 정리한다.
    """
    tmp = f"{path}.ai-tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        # mtime 비교는 교체 직전에 한다. 쓰기 전에 비교하면, 비교를 통과한
        # writer들이 tmp를 쓰는 동안 창이 열려 전부 교체에 성공하고
        # 마지막 것만 남아 나머지가 조용히 사라진다 (동시 12개 실측).
        try:
            current = os.path.getmtime(path)
        except OSError:
            current = None
        if expected_mtime is not None and current != expected_mtime:
            return False
        os.replace(tmp, path)
        return True
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _acquire_lock(path, timeout=20.0, stale_after=15.0):
    """데이터 파일 옆의 .lock 파일로 writer를 직렬화한다 (프로세스 간).

    mtime 낙관적 검사만으로는 동시 writer를 막지 못한다: 비교와 교체
    사이의 창에서 여러 writer가 같은 세대를 보고 전부 성공해 서로를
    덮는다 (2026-11-05 동시 12개 실측에서 다수 소실). 그래서 mutate는
    읽기-수정-쓰기 전체를 이 잠금으로 감싼다. 잠금을 건 프로세스가
    죽으면 stale_after 초가 지난 잠금은 낡은 것으로 보고 깨뜨린다.
    제한 시간 안에 못 잡으면 None을 돌려주고, 호출부는 잠금 없이
    진행한다 (mtime 검사가 최후의 방어선으로 남는다)."""
    lock_path = path + ".lock"
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            except OSError:
                pass
            return (fd, lock_path)
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_path)
            except OSError:
                age = 0.0
            if age > stale_after:
                try:
                    os.remove(lock_path)
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                return None
            time.sleep(0.05)
        except OSError:
            return None


def _release_lock(lock):
    if not lock:
        return
    fd, lock_path = lock
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.remove(lock_path)
    except OSError:
        pass


def mutate(path, fn):
    """최신 파일을 읽어 fn(payload)->result를 적용하고 저장. 충돌 시 재시도.

    mtime은 payload를 읽기 "전"에 샘플링한다 (읽은 뒤에 샘플링하면 그
    사이 저장된 변경이 검사에서 빠진다). 여기에 _acquire_lock의 직렬화를
    겹쳐, 동시 writer가 서로의 변경을 덮지 않게 한다."""
    lock = _acquire_lock(path)
    try:
        for _ in range(15):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = None
            payload = load_payload(path)
            result = fn(payload)
            if save_payload(path, payload, mtime):
                return result
            time.sleep(0.1 + random.random() * 0.3)
        raise SystemExit("다른 저장과 계속 충돌합니다. 잠시 후 다시 시도하세요.")
    finally:
        _release_lock(lock)


def is_locked(note):
    # 잠금 판정은 locked 플래그만으로 한다 (fail-closed). 예전에는 pw_hash까지
    # 있어야 잠금으로 봐서, locked=true인데 해시가 비어 있는 메모(파일 편집,
    # 가져오기·병합 경로에서 생길 수 있는 상태)는 앱 화면에서 잠겨 보이는데도
    # MCP/CLI가 본문과 실제 제목을 그대로 돌려주는 구멍이 있었다
    # (2026-11-05 정보보안팀 침투 실측). 앱 쪽 판정(Note.is_locked_now)도
    # 플래그만 보므로 기준을 여기에 맞춘다.
    return bool(note.get("locked"))


def title_of(note):
    if is_locked(note):
        return "🔒 비밀 메모"
    for line in (note.get("text") or "").splitlines():
        if line.strip():
            return line.strip()
    return "새 메모"


def public_view(note, with_body=True):
    locked = is_locked(note)
    view = {
        "id": note.get("id"),
        "title": title_of(note),
        "folder": note.get("folder") or "",
        "starred": bool(note.get("starred")),
        "archived": bool(note.get("archived")),
        "locked": locked,
        "created_at": note.get("created_at") or "",
        "updated_at": note.get("updated_at") or "",
        "origin": note.get("origin", "user"),
        "ai_name": note.get("ai_name", ""),
        "ai_at": note.get("ai_at", ""),
    }
    if with_body:
        view["text"] = "" if locked else (note.get("text") or "")
    return view


def view_h_for(text):
    lines = text.count("\n") + 1
    return min(480, max(140, 96 + lines * 18))


def new_ai_note(text, folder="AI", starred=False, archived=True, ai_name=None):
    """AI가 추가하는 노트 dict를 만든다.

    기본값은 보관함(archived=True)이며, 화면 표시 여부는 호출자가 정한다.
    출처(origin/ai_name/ai_at)는 항상 기록하고, ai_name이 없으면
    환경변수 MINIMEMO_AI_NAME, 그래도 없으면 'AI'로 남긴다.
    """
    return {
        "id": uuid.uuid4().hex[:12],
        "text": text,
        "color": "노랑",
        "folder": folder,
        "starred": bool(starred),
        "locked": False, "pw_hash": "",
        "pos_lock": False, "alpha": 1.0,
        "font_size": 9,
        "alarm_at": "",
        "created_at": now_str(), "updated_at": now_str(),
        "history": [],
        "x": 24, "y": 24,
        "view_w": 210, "view_h": view_h_for(text),
        "topmost": False,
        "archived": bool(archived),
        "origin": "ai",
        "ai_name": ai_name or os.environ.get("MINIMEMO_AI_NAME") or "AI",
        "ai_at": now_str(),
    }
