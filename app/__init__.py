import os

from flask import Flask

from app.extensions import db, login_manager


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "limeslake-dev-secret")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://limeslake:limeslake@127.0.0.1:6130/limeslake",
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))

    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.board import bp as board_bp
    from app.blueprints.batches import bp as batches_bp
    from app.blueprints.crosscheck import bp as crosscheck_bp
    from app.blueprints.ponds import bp as ponds_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(board_bp)
    app.register_blueprint(crosscheck_bp)
    app.register_blueprint(ponds_bp)
    app.register_blueprint(batches_bp)

    @app.route("/")
    def index():
        from flask import redirect, url_for
        from flask_login import current_user

        if current_user.is_authenticated:
            return redirect(url_for("board.floor_plan"))
        return redirect(url_for("auth.login"))

    return app


def seed_demo_data() -> None:
    from datetime import timedelta

    from app.models import Plant, Pond, SlakeBatch, User, utcnow

    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", role="admin")
        admin.set_password("123456")
        db.session.add(admin)
    else:
        admin = User.query.filter_by(username="admin").first()
        admin.set_password("123456")
        admin.role = "admin"

    if not User.query.filter_by(username="worker").first():
        worker = User(username="worker", role="worker")
        worker.set_password("123456")
        db.session.add(worker)
    else:
        worker = User.query.filter_by(username="worker").first()
        worker.set_password("123456")
        worker.role = "worker"

    # 种子幂等：已有厂区保留不动，缺哪座厂补哪座（旧库重启也能拿到第二座厂）。
    plant = Plant.query.filter_by(name="东湾石灰厂").first()
    plant2 = Plant.query.filter_by(name="西岭石灰厂").first()
    new_ponds = []

    if plant is None:
        plant = Plant(name="东湾石灰厂", location="江北码头侧", notes="熟化池示范厂区")
        db.session.add(plant)
        db.session.flush()
        pid = plant.id
        new_ponds += [
            Pond(plant_id=pid, code="P-01", status=Pond.STATUS_SLAKING, capacity_m3=48.0),
            Pond(plant_id=pid, code="P-02", status=Pond.STATUS_FILLING, capacity_m3=36.0),
            Pond(plant_id=pid, code="P-03", status=Pond.STATUS_DRAWN, capacity_m3=40.0),
            Pond(plant_id=pid, code="P-04", status=Pond.STATUS_SLAKING, capacity_m3=42.0),
            Pond(plant_id=pid, code="P-05", status=Pond.STATUS_FILLING, capacity_m3=38.0),
            Pond(plant_id=pid, code="P-06", status=Pond.STATUS_DRAWN, capacity_m3=44.0),
        ]

    if plant2 is None:
        plant2 = Plant(name="西岭石灰厂", location="西岭矿山东侧", notes="第二座示范厂区")
        db.session.add(plant2)
        db.session.flush()
        pid2 = plant2.id
        new_ponds += [
            Pond(plant_id=pid2, code="X-01", status=Pond.STATUS_FILLING, capacity_m3=35.0),
            Pond(plant_id=pid2, code="X-02", status=Pond.STATUS_SLAKING, capacity_m3=46.0),
            Pond(plant_id=pid2, code="X-03", status=Pond.STATUS_DRAWN, capacity_m3=41.0),
            Pond(plant_id=pid2, code="X-04", status=Pond.STATUS_SLAKING, capacity_m3=49.0),
        ]

    if not new_ponds:
        db.session.commit()
        return

    db.session.add_all(new_ponds)
    db.session.flush()
    ponds_by_code = {pond.code: pond for pond in new_ponds}

    now = utcnow()
    batch_specs = [
        ("P-01", timedelta(hours=6), 85.0, 72.0, "峰值已过，可出灰"),
        ("P-02", timedelta(hours=2), 80.0, None, "注水中，尚未测得峰值"),
        ("P-03", timedelta(days=1), 82.0, 91.0, "已出灰批次"),
        ("P-04", timedelta(hours=9), 84.0, 66.0, "熟化中段"),
        ("P-05", timedelta(hours=1), 80.0, None, "刚开池注水"),
        ("P-06", timedelta(days=2), 83.0, 88.0, "东侧池已出灰"),
        ("X-01", timedelta(minutes=40), 80.0, None, "西岭厂刚注水"),
        ("X-02", timedelta(hours=5), 82.0, 68.0, "熟化中"),
        ("X-03", timedelta(days=1, hours=4), 83.0, 90.0, "已出灰"),
        ("X-04", timedelta(hours=8), 84.0, 64.0, "熟化中"),
    ]
    db.session.add_all(
        [
            SlakeBatch(
                pond=ponds_by_code[code],
                started_at=now - offset,
                target_temp_c=target,
                peak_temp_c=peak,
                notes=note,
            )
            for code, offset, target, peak, note in batch_specs
            if code in ponds_by_code
        ]
    )
    db.session.commit()
