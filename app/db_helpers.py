from sqlalchemy.orm import Session

from app.models import (
    Producto,
    ProductoSupermercadoGlobal as PSGModel,
    MapeoTicketProducto,
)


def db_search(db: Session, q: str, supermarket: str = None) -> list:
    nq = f"%{q}%"
    try:
        if supermarket:
            rows = (
                db.query(Producto, PSGModel.price_ref)
                .join(PSGModel, Producto.barcode == PSGModel.barcode)
                .filter(
                    PSGModel.supermarket == supermarket,
                    Producto.allowed == True,
                    Producto.name.ilike(nq),
                )
                .order_by(Producto.name)
                .limit(12)
                .all()
            )
            return [
                {"barcode": p.barcode, "name": p.name, "category": p.category,
                 "allowed": p.allowed, "price_ref": float(pr) if pr else None}
                for p, pr in rows
            ]
        else:
            rows = (
                db.query(Producto)
                .filter(Producto.allowed == True, Producto.name.ilike(nq))
                .order_by(Producto.name)
                .limit(12)
                .all()
            )
            return [
                {"barcode": p.barcode, "name": p.name, "category": p.category,
                 "allowed": p.allowed, "price_ref": None}
                for p in rows
            ]
    except Exception as e:
        print(f"[DB] Search error: {e}")
        return []


def db_get_by_barcode(db: Session, barcode: str) -> dict | None:
    try:
        p = db.query(Producto).filter(Producto.barcode == barcode).first()
        if not p:
            return None
        return {"barcode": p.barcode, "name": p.name, "category": p.category,
                "allowed": p.allowed, "source": p.source}
    except Exception as e:
        print(f"[DB] Barcode lookup error: {e}")
        return None


def db_upsert_product(
    db: Session, barcode: str, name: str, category: str, allowed: bool, source: str = "off"
):
    try:
        existing = db.query(Producto).filter(Producto.barcode == barcode).first()
        if existing:
            existing.name = name
            existing.category = category
            existing.allowed = allowed
        else:
            db.add(Producto(barcode=barcode, name=name, category=category,
                            allowed=allowed, source=source))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[DB] Upsert error: {e}")


def db_all_products(db: Session, supermarket: str = None) -> list:
    try:
        if supermarket:
            rows = (
                db.query(Producto, PSGModel.price_ref, PSGModel.available)
                .outerjoin(
                    PSGModel,
                    (Producto.barcode == PSGModel.barcode) & (PSGModel.supermarket == supermarket),
                )
                .order_by(Producto.category, Producto.name)
                .all()
            )
            return [
                {"barcode": p.barcode, "name": p.name, "category": p.category,
                 "allowed": p.allowed, "source": p.source,
                 "price_ref": float(pr) if pr else None, "available": av}
                for p, pr, av in rows
            ]
        else:
            rows = db.query(Producto).order_by(Producto.category, Producto.name).all()
            return [
                {"barcode": p.barcode, "name": p.name, "category": p.category,
                 "allowed": p.allowed, "source": p.source, "price_ref": None, "available": None}
                for p in rows
            ]
    except Exception as e:
        print(f"[DB] List error: {e}")
        return []


def db_get_barcode_by_mapping(db: Session, supermarket: str, raw_ticket_name: str) -> str | None:
    try:
        m = (
            db.query(MapeoTicketProducto)
            .filter(
                MapeoTicketProducto.supermarket == supermarket.strip().lower(),
                MapeoTicketProducto.raw_ticket_name == raw_ticket_name.strip().lower(),
            )
            .first()
        )
        return m.barcode if m else None
    except Exception as e:
        print(f"[DB] Error looking up ticket mapping: {e}")
        return None


def db_save_ticket_mapping(db: Session, supermarket: str, raw_ticket_name: str, barcode: str):
    try:
        sm = supermarket.strip().lower()
        rtn = raw_ticket_name.strip().lower()
        existing = (
            db.query(MapeoTicketProducto)
            .filter(
                MapeoTicketProducto.supermarket == sm,
                MapeoTicketProducto.raw_ticket_name == rtn,
            )
            .first()
        )
        if existing:
            existing.barcode = barcode.strip()
        else:
            db.add(MapeoTicketProducto(supermarket=sm, raw_ticket_name=rtn, barcode=barcode.strip()))
        db.commit()
        print(f"[DB] Mapeo guardado: [{supermarket}] '{raw_ticket_name}' -> '{barcode}'")
    except Exception as e:
        db.rollback()
        print(f"[DB] Error saving ticket mapping: {e}")
