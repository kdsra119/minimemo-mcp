# 미니메모 (MiniMemo)

Windows용 초소형 메모 앱입니다. 메모를 제목만 보이는 최소 상태(라벨)로 접어두면 화면을 거의 차지하지 않습니다. Microsoft Store에서 무료로 내려받을 수 있습니다.

## 핵심 특징

- **화면을 거의 차지하지 않는 메모**: 제목만 보이는 라벨 상태로 접어두어 화면을 거의 차지하지 않습니다.
- **로컬 저장**: 데이터는 로컬 파일에만 저장되며, 자체 동기화 서버가 없습니다.
- **잠긴 메모 보호**: 잠긴 메모는 AI에게 본문이 보이지 않고 제목도 "비밀 메모"로 가려집니다.
- **MCP 브리지**: Claude 같은 AI가 메모를 읽고 쓸 수 있게 해주는 MCP 서버를 제공합니다.

## MCP 브리지

MCP 브리지는 stdio 방식의 MCP 서버로, 로컬에서만 동작하며 별도의 서버가 필요 없습니다. AI에게 노출되는 도구는 6종입니다.

| 도구 | 종류 | 설명 |
| --- | --- | --- |
| `notes_list` | 읽기 | 메모 목록을 최근 수정순으로 반환합니다 (본문 제외). |
| `notes_search` | 읽기 | 제목과 본문에서 검색어를 찾습니다. |
| `notes_get` | 읽기 | id로 메모 1건을 반환합니다. |
| `notes_add` | 쓰기 | 새 메모를 추가합니다 (기본은 보관함). |
| `notes_archive` | 쓰기 | 메모를 보관함으로 보냅니다 (삭제가 아님). |
| `notes_activate` | 쓰기 | 보관함의 메모를 화면 표시 상태로 되돌립니다. |

메모를 수정하거나 삭제하는 도구는 없습니다.

## 무료 vs Pro

| 항목 | 무료 | Pro |
| --- | --- | --- |
| 미니메모 본체의 메모 기능 | 모두 사용 가능 | 모두 사용 가능 |
| AI의 메모 읽기 | 첫 번째로 연결한 AI 1개 | 연결한 모든 AI |
| AI의 메모 쓰기 | 미지원 | 지원 |
| 가격 | 무료 | 일회성 ₩11,000 / $9.99 (구독 아님) |
| 체험 | — | 구매 전 14일간 전 기능 무료 체험 |

## 설치

Microsoft Store에서 무료로 설치할 수 있습니다: https://apps.microsoft.com/detail/9nz9b24ljcjm

## AI 연결 방법

앱 안의 연결 안내를 따라가면 MCP 서버 설정이 자동으로 구성됩니다. 직접 설정이 필요한 경우 아래와 같이 `mcpServers` 항목을 추가합니다. 아래 경로는 예시 표기이며, 실제 설치 위치에 맞게 바꿔 주세요.

```json
{
  "mcpServers": {
    "minimemo": {
      "command": "python",
      "args": ["C:\\MiniMemo\\minimemo_mcp.py"],
      "env": { "MINIMEMO_AI_NAME": "Claude" }
    }
  }
}
```

## 개인정보처리방침

https://kdsra119.github.io/minimemo-docs/

## 라이선스

MIT License 하에 배포됩니다.

## 지원

문의: kdsra119@gmail.com

## English summary

MiniMemo is a tiny notes app for Windows. Notes can be collapsed into title-only labels so they barely take up screen space, and the app is free on Microsoft Store: https://apps.microsoft.com/detail/9nz9b24ljcjm

It includes a local-only, stdio MCP bridge with six tools — `notes_list`, `notes_search`, `notes_get` (read) and `notes_add`, `notes_archive`, `notes_activate` (write). There are no tools to edit or delete notes. Locked notes hide both their body and their title ("비밀 메모") from AI. Data stays in local files; there is no sync server. The free tier covers all note features plus read access for the first connected AI. Pro adds read access from every connected AI and write access, for a one-time ₩11,000 / $9.99 (not a subscription), with a 14-day full-feature trial before purchase. Connect an AI through the in-app guide for automatic setup, or add a `mcpServers` entry pointing at `minimemo_mcp.py`. Privacy policy: https://kdsra119.github.io/minimemo-docs/ · Licensed under the MIT License · Support: kdsra119@gmail.com
