# -*- coding: utf-8 -*-
"""미니메모 MCP 서버 (2단계: stdio, 공식 Python mcp SDK 사용).

연결한 AI가 미니메모의 notes.json을 읽고 쓸 수 있게 하는 MCP 서버.
데이터 접근과 노트 규칙은 minimemo_core가 담당하고, 여기는 도구 노출만 한다.

실행:
  python minimemo_mcp.py [--data-dir DIR]

도구 (전부 notes_ 접두사):
  읽기  notes_list / notes_search / notes_get
  쓰기  notes_add / notes_archive / notes_activate
  수정·삭제 도구는 없다. AI는 기존 메모의 내용을 고치거나 지울 수 없다.

규칙:
- 모든 조회는 코어의 public_view를 거치므로 비밀번호 잠금 메모는
  본문과 실제 제목이 나가지 않는다.
- notes_add 기본값은 보관함이며, 출처(origin/ai_name/ai_at)는 코어가
  강제 기록한다. ai_name은 환경변수 MINIMEMO_AI_NAME(기본 'AI').
- 모든 쓰기 호출은 데이터 폴더의 mcp_audit.jsonl에 1줄씩 남긴다.
  읽기 호출은 기록하지 않는다.
- 환경변수 MINIMEMO_MCP_READONLY=1이면 쓰기 도구를 tools/list에서
  제외한다 (읽기 무료 / 쓰기 Pro 경계의 기술적 토대). 이 동작이
  라이선스 게이트보다 먼저 적용된다.
- 라이선스 게이트(minimemo_license): 쓰기 도구는 Pro/체험 상태에서만
  동작하고, 읽기 도구도 복수 연결 게이트(무료는 AI 1개만)를 받는다.
  차단 시 사유를 문장으로 돌려줘 AI가 사용자에게 설명할 수 있게 한다.
- 환경변수 MINIMEMO_MCP_SELFTEST=1이면 온보딩 연결 테스트 모드다.
  연결 레지스트리에 이름을 남기지 않고 게이트도 점검(probe)으로 받아,
  테스트가 사용자의 실제 AI 연결을 밀어내지 않는다.
"""
import json
import os
import sys
from datetime import datetime

from mcp.server.fastmcp import FastMCP

import minimemo_license
from minimemo_core import (
    is_locked,
    load_payload,
    local_settings,
    mutate,
    new_ai_note,
    now_str,
    public_view,
    resolve_data_file,
)

READONLY = os.environ.get("MINIMEMO_MCP_READONLY") == "1"
SELFTEST = os.environ.get("MINIMEMO_MCP_SELFTEST") == "1"
AI_NAME = os.environ.get("MINIMEMO_AI_NAME") or "AI"

_data_dir = None
for i, arg in enumerate(sys.argv):
    if arg == "--data-dir" and i + 1 < len(sys.argv):
        _data_dir = sys.argv[i + 1]
DATA_FILE = resolve_data_file(_data_dir)
AUDIT_FILE = os.path.join(os.path.dirname(DATA_FILE), "mcp_audit.jsonl")

server = FastMCP("minimemo")


def _require_data_file():
    if not os.path.exists(DATA_FILE):
        raise ValueError("notes.json을 찾을 수 없습니다: " + DATA_FILE)


def _gate(write):
    """라이선스 게이트. 차단은 사유 문장을 도구 오류로 돌려준다.

    판정 자체가 예외로 무너지면 minimemo_license가 fail-open으로
    허용하므로, 여기서 막히는 경우는 확정된 차단 사유뿐이다.
    """
    acc = minimemo_license.check_access(DATA_FILE, AI_NAME, write=write,
                                        probe=SELFTEST)
    if not acc["allowed"]:
        raise ValueError(acc["message"])


def _audit(tool, note_id):
    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
        "note_id": note_id,
        "ai_name": AI_NAME,
    }
    with open(AUDIT_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------- 읽기 도구 ----------

@server.tool()
def notes_list(include_archived: bool = True, folder: str = "") -> dict:
    """미니메모 목록을 최근 수정순으로 돌려준다 (본문 제외).

    include_archived=False면 보관함 메모를 빼고, folder를 주면
    그 폴더의 메모만 돌려준다. 잠긴 메모는 제목이 가려진 채로 나온다.
    """
    _require_data_file()
    _gate(write=False)
    payload = load_payload(DATA_FILE)
    notes = payload.get("notes", [])
    if not include_archived:
        notes = [n for n in notes if not n.get("archived")]
    if folder:
        notes = [n for n in notes if (n.get("folder") or "") == folder]
    notes = sorted(notes, key=lambda n: n.get("updated_at") or "", reverse=True)
    return {"count": len(notes),
            "notes": [public_view(n, with_body=False) for n in notes]}


@server.tool()
def notes_search(query: str) -> dict:
    """제목과 본문에서 검색어를 찾는다.

    잠긴 메모는 본문이 검색 대상에서 제외되며(폴더명만 매칭),
    결과에서도 본문이 나가지 않는다.
    """
    _require_data_file()
    _gate(write=False)
    payload = load_payload(DATA_FILE)
    q = query.lower()
    out = []
    for n in payload.get("notes", []):
        if is_locked(n):
            hay = (n.get("folder") or "").lower()
        else:
            hay = ((n.get("text") or "") + " " + (n.get("folder") or "")).lower()
        if q in hay:
            out.append(public_view(n))
    out.sort(key=lambda v: v["updated_at"], reverse=True)
    return {"count": len(out), "notes": out}


@server.tool()
def notes_get(note_id: str) -> dict:
    """id로 메모 1건을 돌려준다. 잠긴 메모는 본문 없이 메타만 나간다."""
    _require_data_file()
    _gate(write=False)
    payload = load_payload(DATA_FILE)
    for n in payload.get("notes", []):
        if n.get("id") == note_id:
            return public_view(n)
    raise ValueError("해당 id의 메모가 없습니다: " + note_id)


# ---------- 쓰기 도구 (READONLY면 등록 자체를 하지 않는다) ----------

def _set_archived(note_id, archived):
    def do(payload):
        for n in payload.get("notes", []):
            if n.get("id") == note_id:
                n["archived"] = archived
                n["updated_at"] = now_str()
                return public_view(n, with_body=False)
        raise ValueError("해당 id의 메모가 없습니다: " + note_id)

    return mutate(DATA_FILE, do)


def notes_add(text: str, folder: str = "AI", show: bool = False,
              starred: bool = False) -> dict:
    """새 메모를 추가한다. 기본은 보관함 등록이며 show=True면 화면에 바로 표시.

    출처(origin/ai_name/ai_at)는 자동 기록된다.
    """
    _require_data_file()
    _gate(write=True)
    if not text.strip():
        raise ValueError("빈 메모는 만들지 않습니다.")
    # CLI와 같은 규칙: 앱 설정(ai_default_show)이 켜져 있으면 기본이 화면 표시.
    if not show and local_settings().get("ai_default_show"):
        show = True
    note = new_ai_note(text, folder=folder, starred=starred,
                       archived=not show, ai_name=AI_NAME)

    def do(payload):
        payload.setdefault("notes", []).append(note)
        return public_view(note)

    result = mutate(DATA_FILE, do)
    _audit("notes_add", note["id"])
    return result


def notes_archive(note_id: str) -> dict:
    """메모를 보관함으로 보낸다 (삭제가 아니며 언제든 다시 활성화 가능)."""
    _require_data_file()
    _gate(write=True)
    result = _set_archived(note_id, True)
    _audit("notes_archive", note_id)
    return result


def notes_activate(note_id: str) -> dict:
    """보관함의 메모를 화면 표시 상태로 되돌린다."""
    _require_data_file()
    _gate(write=True)
    result = _set_archived(note_id, False)
    _audit("notes_activate", note_id)
    return result


if not READONLY:
    server.tool()(notes_add)
    server.tool()(notes_archive)
    server.tool()(notes_activate)


def main():
    """MCP 서버 실행 진입점. 단독 실행과 minimemo_main.py(--mcp)가 함께 쓴다."""
    # 서버가 동작을 시작했다는 것 자체를 연결 레지스트리에 남긴다
    # (복수 연결 판정의 등장 기록). 실패해도 서버 시작을 막지 않는다.
    # 연결 테스트(SELFTEST)는 레지스트리를 오염시키지 않게 건너뛴다.
    if not SELFTEST:
        try:
            minimemo_license.register_connection(DATA_FILE, AI_NAME)
        except Exception:
            pass
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
