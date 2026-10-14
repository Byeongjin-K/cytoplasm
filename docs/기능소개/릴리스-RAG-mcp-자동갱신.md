# 릴리스 승인으로 RAG 검색 컨테이너 자동 갱신

## 무엇을 · 왜

소유자의 규칙 **"내가 승인한 후에 자꾸 노드에서 작업해야하는거 없으면 좋겠어"**를
RAG 검색에도 적용한다. 예전에는 소스가 동기화돼도 실행 이미지가 그대로여서
노드에서 별도로 MCP 활성화 명령을 실행해야 했다.

이제 릴리스가 RAG 소스와 실행 이미지를 대조한다. stale이면 같은 승인 카드에
`검색 잠깐 끊김(~1분)·되돌림 있음`을 한 번 표시하고, 소스 지문과 릴리스 HEAD를
승인 해시에 묶는다. 최신이면 추가 동작·문구가 없다.

## 사용 시나리오

- 검색 수정이 포함된 릴리스 카드에 소유자가 승인하면, ops가 RAG 노드의 소스를 동기화하고
  **mcp만** 재빌드한다. `/health`와 인증 검색 1회가 통과하면 영수증에 전후 이미지 ID와 성공이
  남는다. 별도 노드 작업은 없다.
- 빌드·health·검색이 실패하면 이전 이미지로 돌아가 재기동하고 검증한다. 실패와 되돌림 결과는
  영수증·적용 통지의 남은 조치에 남는다. 승인이 없거나 다른 HEAD·소스 지문이면 실행하지 않는다.
- 관측할 수 없는 노드는 최신·stale이라고 추측하지 않는다. 경고 후 RAG 갱신만 카드에서 빼고
  릴리스를 진행하며, 미상·승인 누락은 영수증과 적용 통지의 남은 조치에 남긴다.
- 릴리스 PC가 RAG 노드이면 자기 SSH 없이 로컬 ops로 소스·이미지를 관측하고 같은 실행 파일로
  갱신한다. `hostname -s`와 설정의 RAG 노드 이름을 대조하며, 별도 노드일 때만 SSH/SCP를 쓴다.
  테스트·운영용 노드 판정 override는 관측과 배포 모두 `RAG_STACK_LOCAL_NODE`다.

embedding 재빌드는 계속 HELD이며, 이 경로는 embedding·Qdrant를 재기동하지 않는다.
노드 `.env.secrets`는 바이트와 권한을 보존한다. 약 1분은 검색 재기동 창의 안내이며,
빌드 시간은 캐시와 네트워크 상태에 따라 달라진다.

## 관련

`automation/rag_mcp_release.sh`, `automation/rag_stack/{deploy.sh,deploy_runtime.sh,remote.sh,smoke.py}`,
`automation/runtime_package_probe.sh`, `automation/release_approval.py`,
`automation/deploy_receipt.py`, `tests/unit/test_rag_stack_deploy.py`.
별도 승인 카드·워처 없이 기존 릴리스 승인 게이트를 재사용한다.
