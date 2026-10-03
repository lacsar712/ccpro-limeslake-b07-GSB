from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import case, func

from app.extensions import db
from app.models import Plant, Pond
from app.services.rules import RuleError, assert_can_set_pond_status, latest_batch_for_pond

bp = Blueprint("board", __name__, url_prefix="/board")

STATUS_LABELS = {
    Pond.STATUS_FILLING: "注水中",
    Pond.STATUS_SLAKING: "熟化中",
    Pond.STATUS_DRAWN: "已出灰",
}


@bp.route("/")
@login_required
def floor_plan():
    plants = Plant.query.order_by(Plant.name).all()
    plant_id_raw = request.args.get("plant_id", "").strip()
    active_plant = None
    if plant_id_raw.isdigit():
        active_plant = db.session.get(Plant, int(plant_id_raw))
    if active_plant is None and plants:
        active_plant = plants[0]

    ponds = []
    if active_plant:
        ponds = (
            Pond.query.filter_by(plant_id=active_plant.id)
            .order_by(Pond.code)
            .all()
        )

    pond_cards = []
    for pond in ponds:
        batch = latest_batch_for_pond(pond)
        pond_cards.append({"pond": pond, "batch": batch})

    selected_id = request.args.get("pond", type=int)
    selected = None
    selected_batch = None
    if selected_id:
        selected = next((c["pond"] for c in pond_cards if c["pond"].id == selected_id), None)
        if selected:
            selected_batch = latest_batch_for_pond(selected)

    return render_template(
        "board/floor.html",
        plants=plants,
        active_plant=active_plant,
        pond_cards=pond_cards,
        selected=selected,
        selected_batch=selected_batch,
        status_labels=STATUS_LABELS,
    )


@bp.route("/compare")
@login_required
def compare():
    """跨厂只读对照台：全部厂区的池座/熟化中/已出灰座数。

    单条 GROUP BY 聚合查询，实时取库内已提交状态，不做任何缓存或写操作，
    因此平面图抽屉改态提交后，本页三类合计始终与库内一致。
    """
    rows = (
        db.session.query(
            Plant.id,
            Plant.name,
            Plant.location,
            func.count(Pond.id).label("total"),
            func.coalesce(
                func.sum(case((Pond.status == Pond.STATUS_FILLING, 1), else_=0)), 0
            ).label("filling"),
            func.coalesce(
                func.sum(case((Pond.status == Pond.STATUS_SLAKING, 1), else_=0)), 0
            ).label("slaking"),
            func.coalesce(
                func.sum(case((Pond.status == Pond.STATUS_DRAWN, 1), else_=0)), 0
            ).label("drawn"),
        )
        .outerjoin(Pond, Pond.plant_id == Plant.id)
        .group_by(Plant.id, Plant.name, Plant.location)
        .order_by(Plant.name)
        .all()
    )
    totals = {
        "total": sum(row.total for row in rows),
        "filling": sum(row.filling for row in rows),
        "slaking": sum(row.slaking for row in rows),
        "drawn": sum(row.drawn for row in rows),
    }
    return render_template(
        "board/compare.html",
        rows=rows,
        totals=totals,
        status_labels=STATUS_LABELS,
    )


@bp.route("/ponds/<int:pond_id>/ops", methods=["POST"])
@login_required
def pond_ops(pond_id: int):
    pond = Pond.query.get_or_404(pond_id)
    status = request.form.get("status") or pond.status
    peak_raw = (request.form.get("peak_temp_c") or "").strip()
    notes = (request.form.get("batch_notes") or "").strip()

    batch = latest_batch_for_pond(pond)
    if batch is None:
        flash("该池尚无熟化批次，无法登记峰值或出灰", "error")
        return redirect(
            url_for("board.floor_plan", plant_id=pond.plant_id, pond=pond.id)
        )

    if peak_raw:
        try:
            batch.peak_temp_c = float(peak_raw)
        except ValueError:
            flash("峰值温度格式无效", "error")
            return redirect(
                url_for("board.floor_plan", plant_id=pond.plant_id, pond=pond.id)
            )

    batch.notes = notes

    try:
        assert_can_set_pond_status(pond, status)
        pond.status = status
        db.session.commit()
        flash(f"{pond.code} 已更新", "ok")
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("board.floor_plan", plant_id=pond.plant_id, pond=pond.id))
