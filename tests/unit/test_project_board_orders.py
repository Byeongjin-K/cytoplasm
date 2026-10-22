"""도리 지시 (owner 2026-10-21): orders saved under the dashboard's own authority, written back by the
receiving Dori, read by the node gate with its own key only, and never rendered unescaped or exported."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import render_dashboard
from automation.project_board.models import ProjectBoardError, proposal_from_dict
from automation.project_board.orders import OrderStore
from automation.project_board.server import loopback_peer_uid, make_server
from automation.yoodori_gate import yoodori_read_gate as gate

TODAY = date(2026, 10, 21)
TOKEN = "t" * 40
GATE = "g" * 40
ORIGIN = "https://board-host.example-tailnet.ts.net:8810"
HOST = "board-host.example-tailnet.ts.net:8810"
OWNER = "owner@example.com"
EVIL = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'


def _store(tmp_path: Path) -> BoardStore:
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약", "todos": [{"title": "보고서 제출", "due": "2026-10-25"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "요청"},
    }), message_id="m1")
    return store


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[int, BoardStore, OrderStore]]:
    store, orders = _store(tmp_path), OrderStore(tmp_path / "orders.db")
    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=store, today=lambda: TODAY, pending=lambda: (),
        read_source=lambda *a: None, attachment_path=lambda *a: None, on_change=lambda: None,
        tailnet_origins=(ORIGIN,), tailnet_logins=(OWNER,), orders=orders, order_receiver="유돌", gate_token=GATE,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield int(server.server_address[1]), store, orders
    finally:
        server.shutdown()
        server.server_close()


def _fetch(port: int, method: str, path: str, headers: dict[str, str], body: str = "") -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body.encode() if body else None, headers=headers)
    response = connection.getresponse()
    result = response.status, response.read().decode("utf-8", errors="replace")
    connection.close()
    return result


def _form(text: str, todo: str = "", project: str = "") -> str:
    from urllib.parse import urlencode

    return urlencode({"text": text, "todo": todo, "project": project})


LOCAL = {"Cookie": f"pb_token={TOKEN}", "X-PB-Token": TOKEN, "Origin": "http://127.0.0.1:{port}",
         "Content-Type": "application/x-www-form-urlencoded"}
TAILNET = {"Host": HOST, "Tailscale-User-Login": OWNER, "Origin": ORIGIN,
           "Content-Type": "application/x-www-form-urlencoded"}


def _local(port: int) -> dict[str, str]:
    return {**LOCAL, "Origin": LOCAL["Origin"].format(port=port)}


def test_an_order_from_the_owner_is_saved_with_its_todo_project_and_who_wrote_it(served) -> None:
    port, _store, orders = served
    status, body = _fetch(port, "POST", "/order/new", TAILNET, _form("공돌한테 이거 조사시켜\n둘째 줄", todo="pb-1-1"))
    assert status == 200 and json.loads(body)["number"] == 1
    order = orders.get(1)
    assert order is not None
    assert (order.body, order.todo_id, order.project, order.status) == ("공돌한테 이거 조사시켜\n둘째 줄", "pb-1-1", "프로젝트A", "받음")
    assert (order.receiver, order.created_by, order.via) == ("유돌", OWNER, "tailnet")
    status, _ = _fetch(port, "POST", "/order/new", _local(port), _form("로컬 지시", project="프로젝트A"))
    assert status == 200 and orders.get(2).via == "local"  # type: ignore[union-attr]


@pytest.mark.parametrize("headers", [
    {"Content-Type": "application/x-www-form-urlencoded"},  # no cookie, no tailnet login
    {**TAILNET, "Tailscale-User-Login": "someone@example.com"},  # not the allowlisted login
    {**TAILNET, "Origin": "https://evil.example"},  # another site in the owner's browser
    {**TAILNET, "Origin": ""},
])
def test_orders_without_the_dashboards_authority_are_refused(served, headers: dict[str, str]) -> None:
    port, _store, orders = served
    status, _ = _fetch(port, "POST", "/order/new", headers, _form("몰래 지시"))
    assert status in (401, 403)
    assert orders.all() == ()


def test_a_local_order_needs_the_token_and_a_same_origin(served) -> None:
    port, _store, orders = served
    assert _fetch(port, "POST", "/order/new", {**_local(port), "X-PB-Token": "x" * 40}, _form("a"))[0] == 403
    assert _fetch(port, "POST", "/order/new", {**_local(port), "Origin": "https://evil.example"}, _form("a"))[0] == 403
    assert orders.all() == ()


@pytest.mark.parametrize("form", [_form("   "), _form("x" * 1001), _form("a", todo="nope"), _form("a", project="없는 과제")])
def test_empty_long_or_unknown_links_are_refused(served, form: str) -> None:
    port, _store, orders = served
    assert _fetch(port, "POST", "/order/new", TAILNET, form)[0] == 400
    assert orders.all() == ()


def test_the_receiver_writes_back_who_took_it_the_room_post_and_progress(tmp_path: Path) -> None:
    orders = OrderStore(tmp_path / "orders.db")
    first = orders.add("조사해 줘", receiver="유돌", by=OWNER, via="tailnet")
    assert orders.mark_delivered(first.number, "유돌") is True
    assert orders.mark_delivered(first.number, "유돌") is False
    orders.update(first.number, status="넘김", dori="공돌", post_url="https://chat.example/room/1", by="유돌")
    done = orders.update(first.number, status="끝", note="보고서 올림", by="유돌")
    assert (done.status, done.dori, done.post_url, done.note, done.delivered_to) == (
        "끝", "공돌", "https://chat.example/room/1", "보고서 올림", "유돌")
    assert [e.kind for e in done.events] == ["created", "delivered", "update", "update"]
    for bad in ({"status": "완료"}, {"post_url": "javascript:alert(1)"}, {"post_url": "https://a b"}, {}):
        with pytest.raises(ProjectBoardError):
            orders.update(first.number, by="유돌", **bad)
    with pytest.raises(ProjectBoardError):
        orders.update(99, status="끝", by="유돌")


def test_the_gate_reads_an_order_with_its_own_key_only(served) -> None:
    port, _store, orders = served
    orders.add("메일 정리해 줘", receiver="유돌", by=OWNER, via="tailnet")
    key = {"X-PB-Gate-Token": GATE}
    status, body = _fetch(port, "GET", "/api/orders/1", key)
    data = json.loads(body)
    assert status == 200
    assert data["body_sha256"] == hashlib.sha256("메일 정리해 줘".encode()).hexdigest()
    assert (data["created_by"], data["via"], data["status"], data["uses"]) == (OWNER, "tailnet", "받음", 0)
    assert "body" not in data
    assert _fetch(port, "POST", "/api/orders/1/use", key)[0] == 200
    assert json.loads(_fetch(port, "GET", "/api/orders/1", key)[1])["uses"] == 1
    for headers in ({}, {"X-PB-Gate-Token": TOKEN}, {**key, "Tailscale-User-Login": OWNER}, {**key, "Host": HOST},
                    {"Cookie": f"pb_token={TOKEN}"}):
        assert _fetch(port, "GET", "/api/orders/1", headers)[0] == 403
    assert _fetch(port, "GET", "/api/orders/9", key)[0] == 404
    assert orders.get(1).uses == 1  # type: ignore[union-attr]


def test_order_words_are_escaped_everywhere_and_never_exported(tmp_path: Path) -> None:
    store, orders = _store(tmp_path), OrderStore(tmp_path / "orders.db")
    orders.add(EVIL, receiver="유돌", by=OWNER, via="tailnet", todo_id="pb-1-1", project="프로젝트A")
    orders.update(1, dori=EVIL[:20], note=EVIL[:300], by="유돌")
    snapshot = store.snapshot(today=TODAY)
    page = render_dashboard(snapshot, mode="served", token=TOKEN, orders=orders.all(), order_receiver="유돌")
    assert "<script>alert(1)" not in page and "<img src=x" not in page
    assert page.count("&lt;script&gt;alert(1)") >= 3  # summary box, list, detail screen
    assert 'data-tab="order" data-key="1"' in page and 'href="#order/1"' in page
    assert 'data-order-todo="pb-1-1"' in page  # the todo screen offers an order about it
    exported = render_dashboard(snapshot, mode="file", orders=orders.all(), order_receiver="유돌")
    assert "도리에게 지시" not in exported and "alert(1)" not in exported


def test_an_order_reads_once_and_its_result_stays_in_sight(tmp_path: Path) -> None:
    """orders-ui-qa (owner 2026-10-22 "지시하는 UI가 좀 이상해"): the receiver is named once, the sender's login is not
    shown as a person, the gate's uses are one line, a finished order's memo is on the summary when nothing is
    open, and the project screen offers an order about that project."""
    import re

    store, orders = _store(tmp_path), OrderStore(tmp_path / "orders.db")
    orders.add("조사해 줘", receiver="유돌", by=OWNER, via="tailnet", todo_id="pb-1-1", project="프로젝트A")
    orders.mark_delivered(1, "유돌")
    orders.record_use(1, by="gate")
    orders.record_use(1, by="gate")
    orders.update(1, status="끝", note="보고서 올림", by="유돌")
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token=TOKEN, orders=orders.all(),
                            order_receiver="유돌")
    detail = page[page.index('data-tab="order" data-key="1"'):]
    detail = detail[:detail.index("</section>")]
    receiver = re.search(r"<dt>받는 도리</dt><dd>(.*?)</dd>", detail)
    assert receiver is not None and receiver.group(1).count("유돌") == 1
    assert OWNER not in detail
    assert detail.count("쓰기 근거로 확인됨") == 1 and "(2번)" in detail
    box = page[page.index('id="order-box"'):]
    box = box[:box.index("</section>")]
    assert 'href="#order/1"' in box and "보고서 올림" in box
    assert 'data-order-project="프로젝트A"' in page


def test_the_summary_box_is_short_trials_stay_apart_and_memos_read_as_plain_words(tmp_path: Path) -> None:
    """orders-ui-fix2 (owner 2026-10-22 "안 고쳐졌는데? 도리에게 지시 탭이 부자연스러워"): the summary box holds the open
    orders and one latest result, never a trial; trials keep their own fold and screen; a memo drops status codes and
    gate talk; a linked todo sits on its own line, not inside the dot-joined meta line."""
    store, orders = _store(tmp_path), OrderStore(tmp_path / "orders.db")
    for words in ("[시험] 경로 확인", "표 정리해 줘", "일정 잡아 줘"):
        orders.add(words, receiver="유돌", by=OWNER, via="tailnet", todo_id="pb-1-1")
    orders.mark_delivered(1, "시험(보내지 않음)")
    orders.update(1, status="끝", note="시험 지시, 바로 닫음", by="유돌")
    orders.mark_delivered(2, "유돌")
    orders.update(2, status="끝", note="표 올림(200) 바로 지움(204), 대시보드 지시 근거로 게이트 통과", by="유돌")
    page = render_dashboard(store.snapshot(today=TODAY), mode="served", token=TOKEN, orders=orders.all(),
                            order_receiver="유돌")

    def section(marker: str) -> str:
        part = page[page.index(marker):]
        return part[:part.index("</section>")]

    box = section('id="order-box"')
    assert 'href="#order/3"' in box and 'href="#order/2"' in box and "#order/1" not in box
    assert "표 올림 바로 지움" in box and "(200)" not in box and "게이트" not in box
    assert "지시 탭에서 전부 보기" in box and 'href="#todo' not in box
    # orders-ui-fold (owner 2026-10-22 "도리에게 지시도 접을수있게"): the box folds by its header, which keeps the open
    # count, the one line and the write button in sight while it is folded
    head = re.search(r'<details [^>]*data-fold="orders-box"[^>]*><summary class="fold-head">(.*?)</summary>', box)
    assert head is not None and '<span class="badge d-none cnt">1</span>' in head.group(1)
    assert "진행 중 1건" in head.group(1) and 'href="#orders">지시 쓰기</a>' in head.group(1)
    tab = section('id="orders"')
    trial = tab[tab.index('data-fold="orders-trial"'):]
    assert 'href="#order/1"' in trial and 'href="#order/1"' not in tab[:tab.index('data-fold="orders-trial"')]
    assert "시험이라 보내지 않음" in trial and "시험(보내지 않음)" not in page
    metas = re.findall(r'<span class="where">(.*?)</span><', tab)
    assert len(metas) == 3 and not any("#todo" in meta for meta in metas)
    assert 'class="order-rel"' in tab and "유돌이 끝냄" in tab
    detail = section('data-tab="order" data-key="2"')
    assert "표 올림 바로 지움" in detail and "(204)" not in detail and "게이트 통과" not in detail
    assert orders.get(2).note.endswith("게이트 통과")  # type: ignore[union-attr]  # the store keeps the words


def test_the_order_todo_list_puts_work_in_progress_first_and_every_dropdown_gets_the_find_field(tmp_path: Path) -> None:
    """Owner 2026-10-22 "드롭 다운이 너무 길어 ... 모든 드롭 다운에 동일한 개선안": in-progress todos lead their project
    group, then the nearest deadline; the find field script ships in the served page and the exported file alike."""
    store = BoardStore(tmp_path / "board.db")
    store.apply_proposal("pb-1", proposal_from_dict({
        "project": "프로젝트A", "summary": "요약", "todos": [
            {"title": "나중 마감", "due": "2026-11-30"}, {"title": "곧 마감", "due": "2026-10-23"},
            {"title": "하는 중", "due": "2026-12-31"}],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "요청"},
    }), message_id="m1")
    store.record_progress("pb-1-3", status="진행", by=OWNER)
    snapshot = store.snapshot(today=TODAY)
    page = render_dashboard(snapshot, mode="served", token=TOKEN, orders=(), order_receiver="유돌")
    group = page.split('<optgroup label="프로젝트A">', 1)[1].split("</optgroup>", 1)[0]
    assert group.index("하는 중") < group.index("곧 마감") < group.index("나중 마감")
    for mode in ("served", "file"):
        assert "PB.find=function" in render_dashboard(snapshot, mode=mode, token=TOKEN)


def test_the_yoodori_gate_writes_order_status_through_fixed_argv() -> None:
    name, argv, cwd = gate.build(["order-status", "12", "처리 중", "--dori", "공돌",
                                  "--post-url", "https://chat.example/room/9", "--note", "조사 시작"])
    assert (name, cwd) == ("order-status", gate.RELEASE)
    assert argv == ["/usr/bin/python3", "-m", "automation.project_board", "order-update", "12", "--status", "처리 중",
                    "--dori", "공돌", "--post-url", "https://chat.example/room/9", "--note", "조사 시작", "--by", "유돌"]
    for words in (["order-status", "0", "끝"], ["order-status", "1", "완료"], ["order-status", "1", "끝", "--by", "형"],
                  ["order-status", "1", "끝", "--post-url", "javascript:alert(1)"],
                  ["order-status", "1", "끝", "--dori", "x" * 21], ["order-status", "1", "끝", "--note"]):
        with pytest.raises(gate.Refused):
            gate.build(words)


PHONE, PHONE_IP, SERVER, SERVER_IP = "nPhoneAAAA11CNTRL", "192.0.2.23", "nServerBBB11CNTRL", "192.0.2.95"
WHOIS = {PHONE_IP: (PHONE, "iphone"), SERVER_IP: (SERVER, "node-a")}


def _device_server(tmp_path: Path, peer: int | None) -> tuple[object, int, OrderStore]:
    store, orders = _store(tmp_path), OrderStore(tmp_path / "orders.db")
    server = make_server(
        host="127.0.0.1", port=0, token=TOKEN, store=store, today=lambda: TODAY, pending=lambda: (),
        read_source=lambda *a: None, attachment_path=lambda *a: None, on_change=lambda: None,
        tailnet_origins=(ORIGIN,), tailnet_logins=(OWNER,), orders=orders, order_receiver="유돌", gate_token=GATE,
        owner_devices=((PHONE, "형 폰"),), peer_uid=lambda client, srv: peer, whois=WHOIS.get,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1]), orders


@pytest.mark.parametrize("peer, sent_from, via, device", [
    (0, PHONE_IP, "owner-device", PHONE),      # tailscale serve (root) says the owner's phone sent it
    (0, SERVER_IP, "tailnet", SERVER),          # the node itself on the owner's account is not the owner
    (0, "192.0.2.9", "tailnet", ""),           # an address whois does not know
    (1001, PHONE_IP, "tailnet", ""),            # a local process forging the header: not tailscaled, not believed
    (None, PHONE_IP, "tailnet", ""),            # peer socket not found: not believed
])
def test_only_an_owner_device_through_tailscale_serve_is_the_owners_command(
    tmp_path: Path, peer: int | None, sent_from: str, via: str, device: str,
) -> None:
    server, port, orders = _device_server(tmp_path, peer)
    try:
        status, _ = _fetch(port, "POST", "/order/new", {**TAILNET, "X-Forwarded-For": sent_from}, _form("지시"))
        assert status == 200
        data = json.loads(_fetch(port, "GET", "/api/orders/1", {"X-PB-Gate-Token": GATE})[1])
        assert (data["via"], data["device"]) == (via, device)
        assert orders.get(1).via == via  # type: ignore[union-attr]
    finally:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]


def test_a_forged_login_from_a_non_owner_device_is_still_refused(tmp_path: Path) -> None:
    server, port, orders = _device_server(tmp_path, 0)
    try:
        headers = {**TAILNET, "Tailscale-User-Login": "someone@example.com", "X-Forwarded-For": PHONE_IP}
        assert _fetch(port, "POST", "/order/new", headers, _form("지시"))[0] == 403
        assert orders.all() == ()
    finally:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]


def test_the_peer_socket_owner_is_read_from_the_kernel_table() -> None:
    import os
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname())
    accepted, _ = listener.accept()
    try:
        assert loopback_peer_uid(client.getsockname()[1], listener.getsockname()[1]) == os.getuid()
        assert loopback_peer_uid(client.getsockname()[1], 1) is None
    finally:
        for sock in (accepted, client, listener):
            sock.close()


def test_owner_devices_config_takes_stable_node_ids_only(tmp_path: Path) -> None:
    from automation.project_board.config import load_config

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"dashboard": {"tailnet": {"owner_devices": [{"id": PHONE, "name": "형 폰"}]}}}))
    assert load_config({"PROJECT_BOARD_CONFIG": str(path)}).owner_devices == ((PHONE, "형 폰"),)
    path.write_text(json.dumps({"dashboard": {"tailnet": {"owner_devices": [{"id": PHONE_IP}]}}}))
    with pytest.raises(ProjectBoardError):
        load_config({"PROJECT_BOARD_CONFIG": str(path)})
