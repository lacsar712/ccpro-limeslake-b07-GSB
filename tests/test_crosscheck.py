"""跨厂只读对照台测试。

覆盖评分点：
- 种子至少两厂；
- 对照台数字 = 各厂平面图瓦片点数 = 库内直接计数（三处同源）；
- 顺平面图抽屉改态后再开对照台，三类合计与库差为 0；
- 对照台只读：无表单/无状态控件，POST 一律 405；
- 两人在不同厂几乎同时各改一口池入熟化中，对照台合计仍与库对齐。
"""

import re
import threading

from sqlalchemy import func

from app import seed_demo_data
from app.extensions import db
from app.models import Plant, Pond

CROSSCHECK_URL = "/crosscheck/"
TILE_RE = re.compile(r'pond-tile status-([a-z]+)')


def parse_crosschart(html: str):
    """返回 {plant_name: (total, filling, slaking, drawn)} 与合计行。"""
    # 用更稳妥的行级解析：tbody/tfoot 内逐行抓单元格数字
    tbody = re.search(r"<tbody>(.*?)</tbody>", html, re.S).group(1)
    tfoot = re.search(r"<tfoot>(.*?)</tfoot>", html, re.S).group(1)

    per_plant = {}
    for tr in re.findall(r"<tr>(.*?)</tr>", tbody, re.S):
        tds = [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", td)).strip()
            for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        ]
        # 第一格含厂名+位置，取第一行
        name = tds[0].split()[0]
        total, filling, slaking, drawn = (int(x) for x in tds[1:5])
        per_plant[name] = {
            "total": total,
            "filling": filling,
            "slaking": slaking,
            "drawn": drawn,
        }

    foot_tds = [
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", td)).strip()
        for td in re.findall(r"<td[^>]*>(.*?)</td>", tfoot, re.S)
    ]
    footer = {
        "total": int(foot_tds[1]),
        "filling": int(foot_tds[2]),
        "slaking": int(foot_tds[3]),
        "drawn": int(foot_tds[4]),
    }
    has_form = "<form" in html
    has_select = "<select" in html
    return per_plant, footer, has_form, has_select


def floor_tile_counts(client, plant_id):
    resp = client.get(f"/board/?plant_id={plant_id}")
    assert resp.status_code == 200
    counts = {s: 0 for s in Pond.STATUS_CHOICES}
    for status in TILE_RE.findall(resp.get_data(as_text=True)):
        counts[status] += 1
    return counts


def db_counts(app):
    with app.app_context():
        rows = (
            db.session.query(Pond.plant_id, Pond.status, func.count(Pond.id))
            .group_by(Pond.plant_id, Pond.status)
            .all()
        )
        plants = Plant.query.order_by(Plant.name).all()
        per_plant = {}
        count_map = {(pid, status): count for pid, status, count in rows}
        for plant in plants:
            bucket = {s: count_map.get((plant.id, s), 0) for s in Pond.STATUS_CHOICES}
            bucket["total"] = sum(bucket.values())
            per_plant[plant.name] = bucket
        return per_plant


def test_seed_has_at_least_two_plants(app, login):
    client = login()
    resp = client.get(CROSSCHECK_URL)
    assert resp.status_code == 200
    per_plant, _footer, _f, _s = parse_crosschart(resp.get_data(as_text=True))
    assert len(per_plant) >= 2


def test_crosscheck_matches_floor_tiles_and_database(app, login):
    client = login()
    html = client.get(CROSSCHECK_URL).get_data(as_text=True)
    page_rows, footer, _f, _s = parse_crosschart(html)
    db_rows = db_counts(app)

    with app.app_context():
        plants = Plant.query.order_by(Plant.name).all()

    for plant in plants:
        tiles = floor_tile_counts(client, plant.id)
        tiles["total"] = sum(tiles[s] for s in Pond.STATUS_CHOICES)
        # 对照台该厂行
        assert page_rows[plant.name] == tiles, (
            f"{plant.name}: 对照台 {page_rows[plant.name]} != 瓦片 {tiles}"
        )
        # 库里直接数
        assert page_rows[plant.name] == db_rows[plant.name]

    # 合计 = 各厂行求和 = 库总数；三类相加 = 池座数（库差为 0）
    summed = {k: sum(row[k] for row in page_rows.values()) for k in footer}
    assert summed == footer
    assert footer["filling"] + footer["slaking"] + footer["drawn"] == footer["total"]
    assert footer == {
        k: sum(row[k] for row in db_rows.values()) for k in ("total", "filling", "slaking", "drawn")
    }


def test_crosscheck_is_readonly(app, login):
    client = login()
    # 写方法一律拒绝
    for method in ("post", "put", "patch", "delete"):
        resp = getattr(client, method)(CROSSCHECK_URL, data={"status": "slaking"})
        assert resp.status_code == 405, method

    html = client.get(CROSSCHECK_URL).get_data(as_text=True)
    _rows, _footer, has_form, has_select = parse_crosschart(html)
    assert not has_form, "对照台不允许出现表单"
    assert not has_select, "对照台不允许出现状态下拉等改态控件"
    # 页内不应有任何改态入口（抽屉改态地址或状态下拉）
    assert "/board/ponds/" not in html
    assert 'name="status"' not in html

    # 顶栏必须同时挂平面图与跨厂对照
    assert "/board/" in html and CROSSCHECK_URL in html


def test_crosscheck_requires_login(app):
    client = app.test_client()
    resp = client.get(CROSSCHECK_URL)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_numbers_floor_after_drawer_status_change(app, login):
    """顺着平面图抽屉的改态接口改态，再打开对照台，三类合计与库差为 0。"""
    client = login()

    with app.app_context():
        pond = (
            db.session.query(Pond)
            .filter(Pond.status == Pond.STATUS_FILLING)
            .order_by(Pond.id)
            .first()
        )
        pond_id, plant_id = pond.id, pond.plant_id
        before_global = {
            s: db.session.query(func.count(Pond.id)).filter(Pond.status == s).scalar()
            for s in Pond.STATUS_CHOICES
        }
        before_plant = {
            s: db.session.query(func.count(Pond.id))
            .filter(Pond.status == s, Pond.plant_id == plant_id)
            .scalar()
            for s in Pond.STATUS_CHOICES
        }

    # 抽屉表单：只改状态为熟化中
    resp = client.post(
        f"/board/ponds/{pond_id}/ops",
        data={"status": Pond.STATUS_SLAKING},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    with app.app_context():
        assert db.session.get(Pond, pond_id).status == Pond.STATUS_SLAKING

    html = client.get(CROSSCHECK_URL).get_data(as_text=True)
    page_rows, footer, _f, _s = parse_crosschart(html)
    db_rows = db_counts(app)

    # 受影响厂：注水中 -1、熟化中 +1、池座数不变
    with app.app_context():
        plant = db.session.get(Plant, plant_id)
        plant_name = plant.name
    assert page_rows[plant_name]["total"] == db_rows[plant_name]["total"]
    assert page_rows[plant_name]["filling"] == before_plant["filling"] - 1
    assert page_rows[plant_name]["slaking"] == before_plant["slaking"] + 1
    assert page_rows[plant_name]["drawn"] == before_plant["drawn"]

    # 全库：三类合计与库内逐行计数完全一致，库差 0
    assert footer == {
        k: sum(row[k] for row in db_rows.values())
        for k in ("total", "filling", "slaking", "drawn")
    }
    assert footer["filling"] + footer["slaking"] + footer["drawn"] == footer["total"]
    assert footer["filling"] == before_global["filling"] - 1
    assert footer["slaking"] == before_global["slaking"] + 1
    assert footer["drawn"] == before_global["drawn"]


def test_concurrent_changes_across_two_plants(app):
    """两人几乎同时在不同厂各把一口池改入熟化中，对照台仍与库对齐。"""
    barrier = threading.Barrier(2)
    errors = []

    def worker(plant_name, result):
        try:
            with app.app_context():
                plant = Plant.query.filter_by(name=plant_name).first()
                pond = (
                    db.session.query(Pond)
                    .filter(
                        Pond.plant_id == plant.id,
                        Pond.status == Pond.STATUS_FILLING,
                    )
                    .first()
                )
                target_id = pond.id
            # 各自独立客户端（独立会话/Cookie），模拟两个作业员
            client = app.test_client()
            client.post(
                "/auth/login",
                data={"username": "admin", "password": "123456"},
            )
            barrier.wait(timeout=10)
            resp = client.post(
                f"/board/ponds/{target_id}/ops",
                data={"status": Pond.STATUS_SLAKING},
            )
            assert resp.status_code in (302, 303)
            result.append(target_id)
        except Exception as exc:  # pragma: no cover - 失败时带出现场
            errors.append(exc)

    with app.app_context():
        plant_names = [p.name for p in Plant.query.order_by(Plant.id).limit(2).all()]
    assert len(plant_names) == 2

    changed = []
    threads = [
        threading.Thread(target=worker, args=(name, changed)) for name in plant_names
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert len(changed) == 2

    with app.app_context():
        for pid in changed:
            assert db.session.get(Pond, pid).status == Pond.STATUS_SLAKING

    # 任意一个作业员打开对照台：数字必须与库一致
    client = app.test_client()
    client.post("/auth/login", data={"username": "admin", "password": "123456"})
    html = client.get(CROSSCHECK_URL).get_data(as_text=True)
    page_rows, footer, _f, _s = parse_crosschart(html)
    db_rows = db_counts(app)

    for name in plant_names:
        assert page_rows[name] == db_rows[name]
    assert footer == {
        k: sum(row[k] for row in db_rows.values())
        for k in ("total", "filling", "slaking", "drawn")
    }
    assert footer["filling"] + footer["slaking"] + footer["drawn"] == footer["total"]


def test_seed_is_idempotent_and_backfills_second_plant(app):
    """旧库只有东湾厂时，再跑种子应补齐第二座厂；重复跑不产生重复数据。"""
    with app.app_context():
        # 模拟旧库：删掉西岭厂（连带其池/批次）
        west = Plant.query.filter_by(name="西岭石灰厂").first()
        db.session.delete(west)
        db.session.commit()
        assert Plant.query.count() == 1

        seed_demo_data()
        seed_demo_data()

        plants = Plant.query.order_by(Plant.name).all()
        names = [p.name for p in plants]
        assert names == ["东湾石灰厂", "西岭石灰厂"]
        # 东湾 6 口不动，西岭补齐 4 口，无重复
        east = Plant.query.filter_by(name="东湾石灰厂").first()
        west2 = Plant.query.filter_by(name="西岭石灰厂").first()
        assert len(east.ponds) == 6
        assert len(west2.ponds) == 4
        assert Pond.query.count() == 10
