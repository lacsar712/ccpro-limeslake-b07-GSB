"""跨厂只读对照台。

数字不另存：每次请求都直接对 ponds 表按状态聚合，
与各厂平面图瓦片同源（同一张 Pond 表），抽屉改态后无需同步即一致。
本蓝图只挂 GET，任何写操作一律 405。
"""

from flask import Blueprint, render_template
from flask_login import login_required
from sqlalchemy import func

from app.extensions import db
from app.models import Plant, Pond

bp = Blueprint("crosscheck", __name__, url_prefix="/crosscheck")

STATUS_LABELS = {
    Pond.STATUS_FILLING: "注水中",
    Pond.STATUS_SLAKING: "熟化中",
    Pond.STATUS_DRAWN: "已出灰",
}


def plant_status_rows():
    """每厂三类池座数，全部来自 ponds 表实时聚合。

    返回 [(plant, {"filling": n, "slaking": n, "drawn": n, "total": n}), ...]
    """
    count_rows = (
        db.session.query(Pond.plant_id, Pond.status, func.count(Pond.id))
        .group_by(Pond.plant_id, Pond.status)
        .all()
    )
    counts = {(plant_id, status): count for plant_id, status, count in count_rows}

    plants = Plant.query.order_by(Plant.name).all()
    rows = []
    for plant in plants:
        bucket = {
            status: counts.get((plant.id, status), 0) for status in Pond.STATUS_CHOICES
        }
        bucket["total"] = sum(bucket.values())
        rows.append((plant, bucket))
    return rows


@bp.route("/")
@login_required
def overview():
    rows = plant_status_rows()
    totals = {status: 0 for status in Pond.STATUS_CHOICES}
    totals["total"] = 0
    for _plant, bucket in rows:
        for key in totals:
            totals[key] += bucket[key]
    return render_template(
        "crosscheck/overview.html",
        rows=rows,
        totals=totals,
        status_labels=STATUS_LABELS,
    )
